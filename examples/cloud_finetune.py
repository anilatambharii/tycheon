"""Fine-tune Kronos on your own bars through the Tycheon Cloud API (Enterprise plans).

    pip install httpx
    export TYCHEON_CLOUD_URL=https://your-tycheon-cloud-host        # no trailing path
    export TYCHEON_CLOUD_SESSION=...        # a session token from POST /auth/login (admin role)
    python examples/cloud_finetune.py --csv-dir my_csvs --symbols MYSYM

UNVERIFIED AGAINST A LIVE SERVICE. This script was written from the source of the control plane
(``ee/control_plane/tycheon_cp/app.py``) and the fine-tuning routes
(``ee/finetune/tycheon_ft/routes.py``) and is syntax- and lint-checked only. Nobody has run it
against a running Tycheon Cloud, so field names, status codes and timings may differ from what you
see. Treat the first run as a test and read the HTTP errors it prints.

What it does, in order, stopping at the first failure:

1. ``GET /v1/me``: reads your plan. If the plan does not include ``finetune`` (it is Enterprise
   only) the script **stops and explains**; nothing is uploaded.
2. ``POST /v1/data-sources`` (kind ``csv``), then ``PUT /v1/data-sources/{id}/files/{SYMBOL}``
   for each ``<SYMBOL>.csv`` in ``--csv-dir`` (``--sample`` instead uploads one bundled
   **synthetic** series, only to exercise the pipeline).
3. ``POST /v1/finetune/jobs`` and poll ``GET /v1/finetune/jobs/{id}`` until the job ends.
4. ``GET /v1/models/{id}``: prints the promotion gate. A fine-tuned model is only servable if it
   passed the gate on your own walk-forward test region (clearly lower CRPS than the random walk,
   a significant Diebold-Mariano test, and no worse than the base model). A rejected model is
   kept with its reasons and cannot be served. That outcome is normal and is reported honestly.
5. If ``TYCHEON_CLOUD_API_KEY`` (a ``tyk_...`` data-plane key) is set and the model was promoted,
   requests one forecast with ``model="ft:<id>"``.

Your CSV files are your own data under your own licence; Tycheon never redistributes them. Rows
need a timestamp column (``timestamp``, ``date``, ...) and OHLCV columns, in increasing order, at
least 60 bars.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

DISCLAIMER = "For research and risk analytics. Not investment advice."
TERMINAL = {"succeeded", "failed", "cancelled"}  # finetune_jobs.status CHECK in migration 0001


class StopError(Exception):
    """A step failed or is not available; the message says why."""


def check(response: httpx.Response, what: str) -> Any:
    """Return the JSON body, or raise ``StopError`` with the server's error envelope."""
    if response.is_success:
        return response.json() if response.content else None
    try:
        err = response.json().get("error", {})
        detail = f"{err.get('code')}: {err.get('message')} {err.get('details') or ''}".strip()
    except (ValueError, AttributeError):
        detail = response.text[:300]
    raise StopError(f"{what} failed with HTTP {response.status_code}: {detail}")


def require_finetune(client: httpx.Client) -> dict[str, Any]:
    me = check(client.get("/v1/me"), "GET /v1/me")
    plan = me["plan"]
    print(f"signed in to org {me['org']['slug']!r}, plan {plan['name']!r} ({plan['status']})")
    if "finetune" not in plan["features"]:
        raise StopError(
            f"the {plan['name']} plan does not include fine-tuning (an Enterprise feature), so "
            "there is nothing to do and nothing was uploaded. Ask about the Enterprise plan, or "
            "use the open-source library with the zero-shot models and the baselines."
        )
    return me


def sample_csv() -> tuple[str, bytes]:
    """One bundled synthetic series as CSV: a pipeline test, never a result worth reading."""
    from tycheon.data import load_sample

    bars = load_sample("SYN-GBM")
    return "SYN-GBM", bars.drop(columns=["available_at"]).to_csv().encode()


def upload(client: httpx.Client, csv_dir: Path | None, symbols: list[str]) -> str:
    source = check(
        client.post("/v1/data-sources", json={"name": "finetune-example", "kind": "csv"}),
        "POST /v1/data-sources",
    )
    source_id = source["id"]
    files: list[tuple[str, bytes]] = []
    if csv_dir is None:
        files.append(sample_csv())
    else:
        for symbol in symbols:
            path = csv_dir / f"{symbol}.csv"
            if not path.is_file():
                raise StopError(
                    f"expected {path}; --symbols must match <SYMBOL>.csv files in --csv-dir"
                )
            files.append((symbol, path.read_bytes()))
    for symbol, raw in files:
        info = check(
            client.put(
                f"/v1/data-sources/{source_id}/files/{symbol}",
                content=raw,
                headers={"Content-Type": "text/csv"},
            ),
            f"PUT file {symbol}",
        )
        print(f"uploaded {symbol}: {info['n_rows']} bars, {info['first_ts']} .. {info['last_ts']}")
    return str(source_id)


def wait_for_job(client: httpx.Client, job_id: str, poll: float, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        job = check(client.get(f"/v1/finetune/jobs/{job_id}"), "GET finetune job")
        line = f"job {job['status']}, progress {job.get('progress')}"
        if line != last:
            print(line)
            last = line
        if job["status"] in TERMINAL:
            return job  # type: ignore[no-any-return]
        if time.monotonic() > deadline:
            raise StopError(
                f"gave up waiting after {timeout:.0f}s; the job {job_id} is still running"
            )
        time.sleep(poll)


def report_model(client: httpx.Client, job: dict[str, Any]) -> dict[str, Any]:
    if job["status"] != "succeeded" or not job.get("model_id"):
        raise StopError(f"the job ended as {job['status']}: {job.get('error')}")
    model: dict[str, Any] = check(client.get(f"/v1/models/{job['model_id']}"), "GET model")
    print(f"model {model['id']}: {model['status']}")
    print(json.dumps({"metrics": model.get("metrics"), "gate": model.get("gate")}, indent=2))
    return model


def forecast_with(base_url: str, api_key: str, model: dict[str, Any], symbol: str, h: int) -> None:
    body = {"symbol": symbol, "horizon": h, "model": f"ft:{model['id']}"}
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=120.0) as data_plane:
        forecast = check(data_plane.post("/v1/forecast", json=body), "POST /v1/forecast")
    print(json.dumps(forecast, indent=2)[:2000])


def run(args: argparse.Namespace, base_url: str, session: str) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    headers = {"Authorization": f"Bearer {session}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
        require_finetune(client)
        source_id = upload(client, args.csv_dir, symbols)
        names = symbols or ["SYN-GBM"]
        config = {"horizon": args.horizon, "epochs": args.epochs}
        body = {"data_source_id": source_id, "symbols": names, **config}
        job = check(client.post("/v1/finetune/jobs", json=body), "POST /v1/finetune/jobs")
        print(f"queued fine-tune job {job['id']} on pool {job['gpu_pool']}")
        job = wait_for_job(client, job["id"], args.poll_seconds, args.timeout_seconds)
        model = report_model(client, job)
    if model["status"] != "promoted":
        print(
            "Not promoted, so it cannot be served. It did not clearly beat the random walk and "
            "the base model on your own test region; the reasons are in the gate above. "
            "Publishing that honestly is the point of the gate."
        )
        return
    api_key = os.environ.get("TYCHEON_CLOUD_API_KEY")
    if api_key:
        forecast_with(base_url, api_key, model, names[0], args.horizon)
    else:
        print(f'Promoted. Forecast with model="ft:{model["id"]}" using a tyk_ API key.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv-dir", type=Path, default=None, help="folder of <SYMBOL>.csv files")
    parser.add_argument("--symbols", default="", help="comma-separated symbols in --csv-dir")
    parser.add_argument("--sample", action="store_true", help="upload one synthetic series")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    args = parser.parse_args()

    print(DISCLAIMER)
    base_url = os.environ.get("TYCHEON_CLOUD_URL")
    session = os.environ.get("TYCHEON_CLOUD_SESSION")
    problem = None
    if not base_url or not session:
        problem = "Set TYCHEON_CLOUD_URL and TYCHEON_CLOUD_SESSION (see the docstring)."
    elif args.csv_dir is None and not args.sample:
        problem = "Pass --csv-dir with --symbols, or --sample for a synthetic pipeline test."
    elif args.csv_dir is not None and not args.symbols.strip():
        problem = "--csv-dir needs --symbols."
    if problem or not base_url or not session:
        print(problem)
        print(DISCLAIMER)
        return 2
    code = 0
    try:
        run(args, base_url, session)
    except StopError as exc:
        print(f"stopped: {exc}")
        code = 1
    except httpx.HTTPError as exc:
        print(f"stopped: could not reach {base_url}: {exc}")
        code = 1
    print(DISCLAIMER)
    return code


if __name__ == "__main__":
    sys.exit(main())
