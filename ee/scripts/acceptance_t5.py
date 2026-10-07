"""Phase T5 acceptance, run locally: a new organisation signs up, adds a CSV data source, calls the
forecast and risk endpoints, runs a small fine-tune, sees its usage metered and a Stripe test-mode
invoice preview.

Needs a Postgres (an owner role and an unprivileged application role; see docs/cloud.md) and:

    TYCHEON_CP_MIGRATION_URL   owner role (falls back to TYCHEON_TEST_DATABASE_URL)
    TYCHEON_CP_DATABASE_URL    application role (falls back to TYCHEON_TEST_APP_URL)
    STRIPE_SECRET_KEY          a TEST-mode key; without it the Stripe step is reported as skipped

The data is synthetic and the fine-tune runs on CPU with the real Kronos-mini checkpoint for a few
steps, so a *rejection by the promotion gate is the expected, honest outcome*: a handful of steps on
random-walk data cannot beat the random walk, and the point is that the gate says so.

For research and risk analytics. Not investment advice.

Proprietary: see ee/LICENSE.
"""

# DuckDB first: it cannot share a process with Keelgate's regopy once both are in use.
import asyncio
import base64
import os
import sys
import tempfile
import uuid
from pathlib import Path

import duckdb  # noqa: F401
import httpx
import numpy as np
import pandas as pd

from tycheon_cp.app import make_control_plane
from tycheon_cp.billing_routes import build_billing
from tycheon_cp.config import Settings
from tycheon_cp.db import Database, migrate
from tycheon_cp.factory import create_full_app
from tycheon_ft.loader import PrivateModelResolver
from tycheon_ft.providers import PinnedKronos
from tycheon_ft.worker import WorkerContext, work_once

OPERATOR_TOKEN = "acceptance-operator-token-" + uuid.uuid4().hex
ok_steps: list[str] = []
skipped: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def step(title: str) -> None:
    say(f"\n== {title}")


def csv_bars(n: int = 720) -> str:
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2023-01-02", periods=n, freq="B"),
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": rng.integers(1_000, 50_000, n).astype(float),
        }
    )
    return frame.to_csv(index=False)


def check(response: httpx.Response, expected: int = 200) -> dict:
    if response.status_code != expected:
        say(f"   FAILED: HTTP {response.status_code}: {response.text[:300]}")
        raise SystemExit(1)
    return response.json() if response.content else {}


async def main() -> int:
    owner_url = os.environ.get("TYCHEON_CP_MIGRATION_URL") or os.environ.get(
        "TYCHEON_TEST_DATABASE_URL"
    )
    app_url = os.environ.get("TYCHEON_CP_DATABASE_URL") or os.environ.get("TYCHEON_TEST_APP_URL")
    if not (owner_url and app_url):
        say("Set TYCHEON_CP_MIGRATION_URL and TYCHEON_CP_DATABASE_URL (see docs/cloud.md).")
        return 2

    storage = Path(tempfile.mkdtemp(prefix="tycheon-cloud-"))
    settings = Settings(
        database_url=app_url,
        session_secret=base64.b64encode(os.urandom(36)).decode(),
        storage_root=storage,
        env="dev",
        local_kms_key=base64.b64encode(os.urandom(32)).decode(),
        stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY"),
        operator_token=OPERATOR_TOKEN,
    )
    step("0. migrate and start")
    say(
        f"   applied migrations: {await migrate(owner_url, app_role='tycheon_app') or 'none pending'}"
    )
    db = await Database.connect(app_url)
    cp = make_control_plane(settings, db=db)
    base = PinnedKronos("mini")
    cp.gateway.private_models = PrivateModelResolver(db, cp.blobs, base)
    app = create_full_app(cp)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://cloud.local", timeout=600
    ) as http:
        step("1. a new organisation signs up")
        tag = uuid.uuid4().hex[:8]
        email = f"founder-{tag}@example.com"
        session = check(
            await http.post(
                "/auth/signup",
                json={
                    "org_name": f"Acceptance {tag}",
                    "email": email,
                    "password": "correct horse battery",
                },
            ),
            201,
        )
        auth = {"Authorization": f"Bearer {session['token']}"}
        me = check(await http.get("/v1/me", headers=auth))
        say(
            f"   org {me['org']['slug']} on the {me['plan']['name']} plan, research use only: "
            f"{me['plan']['research_use_only']}"
        )
        ok_steps.append("sign-up")

        step("2. add a CSV data source (synthetic bars, end-of-day)")
        source = check(
            await http.post(
                "/v1/data-sources", json={"name": "my-prices", "default": True}, headers=auth
            ),
            201,
        )
        uploaded = check(
            await http.put(
                f"/v1/data-sources/{source['id']}/files/MYCO", content=csv_bars(), headers=auth
            )
        )
        say(
            f"   MYCO: {uploaded['n_rows']} bars, {uploaded['first_ts'][:10]} to {uploaded['last_ts'][:10]}, "
            f"{uploaded['frequency']}"
        )
        key = check(
            await http.post(
                "/v1/api-keys", json={"name": "acceptance", "scopes": ["analytics"]}, headers=auth
            ),
            201,
        )
        api = {"X-API-Key": key["key"]}
        ok_steps.append("CSV data source")

        step("3. forecast and risk through the API key")
        forecast = check(
            await http.post(
                "/v1/forecast",
                headers=api,
                json={"symbol": "MYCO", "horizon": 5, "model": "random-walk"},
            )
        )
        end = forecast["horizon_end"]
        say(
            f"   forecast as of {forecast['as_of'][:10]}: last close {forecast['last_close']:.2f}, "
            f"5-bar median {end['median']:.2f}, 90% band {end['lower_90']:.2f} to {end['upper_90']:.2f}"
        )
        say(f"   model {forecast['model_id']}, calibration: {forecast['calibration_status']}")
        risk = check(
            await http.post(
                "/v1/risk", headers=api, json={"positions": {"MYCO": 100000.0}, "horizon": 5}
            )
        )
        say(
            f"   risk as of {risk['as_of'][:10]}, portfolio {risk['total_value']:,.0f}, "
            f"calibration: {risk['portfolio_calibration_status']}"
        )
        for row in risk["var_es"][:4]:
            say(
                f"     {row['kind']} {row['level']:.0%}: {row['loss_fraction']:.2%} of portfolio value"
            )
        say(f"   full report handle: {risk['report_id']}")
        ok_steps.append("forecast and risk")

        step("4. a small fine-tune (an operator moves the org to Enterprise terms first)")
        check(
            await http.put(
                f"/operator/orgs/{me['org']['slug']}/subscription",
                json={"plan": "enterprise"},
                headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
            )
        )
        job = check(
            await http.post(
                "/v1/finetune/jobs",
                headers=auth,
                json={
                    "data_source_id": source["id"],
                    "symbols": ["MYCO"],
                    "horizon": 5,
                    "lookback": 64,
                    "epochs": 1,
                    "batch_size": 4,
                    "max_steps": 8,
                    "test_window": 120,
                    "folds": 2,
                    "eval_samples": 30,
                },
            ),
            202,
        )
        say(f"   job {job['id'][:8]} queued on pool {job['gpu_pool']}; running a worker here (CPU)")
        worker = WorkerContext(db=db, blobs=cp.blobs, meter=cp.meter, base=base, device="cpu")
        pool = job["gpu_pool"]
        if not await work_once(worker, pool):
            # not a dedicated pool unless the deployment enables them: claim from the shared pool
            await work_once(worker, "shared")
        done = check(await http.get(f"/v1/finetune/jobs/{job['id']}", headers=auth))
        say(f"   job status: {done['status']} ({done['gpu_seconds']:.0f} s of compute)")
        for model in check(await http.get("/v1/models", headers=auth)):
            gate = model["gate"]
            say(
                f"   model {model['name']}: {model['status'].upper()} ({model['metrics'].get('steps')} steps, "
                f"{model['metrics'].get('device')})"
            )
            for reason in gate.get("reasons", []):
                say(f"     - {reason}")
        ok_steps.append("fine-tune (job run, gate applied)")

        step("5. usage, as metered")
        usage = check(await http.get("/v1/usage", headers=auth))
        for meter in usage["meters"]:
            if meter["used"]:
                say(
                    f"   {meter['kind']}: {meter['used']} {meter['unit']} of {meter['limit']:,} this month"
                )
        ok_steps.append("usage metering")

        step("6. Stripe test-mode invoice preview")
        if not settings.stripe_secret_key:
            say(
                "   SKIPPED: STRIPE_SECRET_KEY (a test-mode key) is not set; nothing here was sent to Stripe."
            )
            skipped.append("Stripe invoice preview")
        else:
            try:
                billing = build_billing(cp)
                await billing.prices()
                preview = check(await http.get("/v1/billing/preview", headers=auth))
                say(
                    f"   upcoming invoice: {preview['total_cents'] / 100:.2f} {preview['currency'].upper()}"
                )
                for line in preview["lines"]:
                    say(f"     - {line['description']}: {line['amount_cents'] / 100:.2f}")
                ok_steps.append("Stripe invoice preview (test mode)")
            except Exception as exc:  # report, never hide: this step talks to a live service
                say(f"   FAILED against Stripe: {type(exc).__name__}: {str(exc)[:200]}")
                skipped.append("Stripe invoice preview (failed)")

    await db.close()
    step("summary")
    say("   verified here: " + "; ".join(ok_steps))
    if skipped:
        say("   NOT verified: " + "; ".join(skipped))
    say("\nFor research and risk analytics. Not investment advice.")
    return 0 if not skipped else 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
