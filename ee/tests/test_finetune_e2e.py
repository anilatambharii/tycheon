"""Fine-tuning end to end: API, queue, worker, gate, registry, serving, and tenant isolation.

The model is a miniature randomly initialised Kronos built from the real vendored classes, so
training, saving, loading and serving run the real code path. Its forecasts are noise, which is what
the promotion gate must refuse; tests that need a *promoted* model replace only the gate's decision
(the gate itself is tested on real scores in ``test_finetune_gate.py``).

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import json
import math
from uuid import UUID

import asyncpg
import pytest

from tycheon_cp import store
from tycheon_ft import registry, worker
from tycheon_ft.dataset import FineTuneConfig, build_windows
from tycheon_ft.gate import GateResult
from tycheon_ft.trainer import TrainingCancelledError, fine_tune
from tycheon_ft.worker import WorkerContext, claim, work_once

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

FORECAST = {"symbol": "MYCO", "horizon": 3, "n_samples": 80, "n_origins": 30, "calibrate": False}
SMALL = {
    "horizon": 5,
    "lookback": 64,
    "epochs": 1,
    "batch_size": 8,
    "max_steps": 6,
    "test_window": 120,
    "folds": 2,
    "eval_samples": 20,
}


async def ready_org(client, signup_org, set_org_plan, csv_factory, *, symbol="MYCO", n=720):
    org = await signup_org()
    await set_org_plan(org.org_id, "enterprise")
    uploaded = await org.upload(symbol, csv_factory(n=n))
    await org.make_key()
    return org, uploaded["source_id"]


def context(cp, tiny_base) -> WorkerContext:
    return WorkerContext(db=cp.db, blobs=cp.blobs, meter=cp.meter, base=tiny_base, device="cpu")


async def submit(client, org, source_id, **over):
    body = {"data_source_id": source_id, "symbols": ["MYCO"], **SMALL, **over}
    return await client.post("/v1/finetune/jobs", json=body, headers=org.session)


async def run_to_completion(cp, tiny_base, org) -> bool:
    return await work_once(context(cp, tiny_base), f"dedicated-{org.org_id}")


def passing_gate(monkeypatch) -> None:
    def fake(*_a, **_k) -> GateResult:
        return GateResult(
            True,
            ["stubbed pass: serving tests only"],
            48,
            {"crps": 1.0},
            {"crps": 2.0},
            {"crps": 1.5},
            {"p_model_better": 0.01},
            {"p_model_better": 0.01},
        )

    monkeypatch.setattr(worker, "run_gate", fake)


# ------------------------------------------------------------------------ submitting
async def test_fine_tuning_is_an_enterprise_feature(
    client, signup_org, set_org_plan, csv_factory
) -> None:
    for plan in ("developer", "startup"):
        org = await signup_org()
        await set_org_plan(org.org_id, plan)
        info = await org.upload("MYCO", csv_factory(n=720))
        res = await submit(client, org, info["source_id"])
        assert res.status_code == 403 and res.json()["error"]["code"] == "plan_feature"


async def test_a_job_is_queued_with_its_parameters(
    client, signup_org, set_org_plan, csv_factory
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    res = await submit(client, org, source)
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["status"] == "queued" and job["symbols"] == ["MYCO"] and job["params"]["epochs"] == 1
    assert job["gpu_pool"] == f"dedicated-{org.org_id}"
    listed = (await client.get("/v1/finetune/jobs", headers=org.session)).json()
    assert [j["id"] for j in listed] == [job["id"]]


@pytest.mark.parametrize(
    "over",
    [
        {"symbols": ["NOPE"]},
        {"epochs": 0},
        {"epochs": 500},
        {"horizon": 99},
        {"unknown": 1},
        {"symbols": []},
        {"learning_rate": 5.0},
    ],
)
async def test_bad_jobs_are_refused_before_queueing(
    client, signup_org, set_org_plan, csv_factory, over
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    assert (await submit(client, org, source, **over)).status_code == 422
    assert (await client.get("/v1/finetune/jobs", headers=org.session)).json() == []


async def test_a_series_too_short_to_fine_tune_is_refused_with_the_numbers(
    client, signup_org, set_org_plan, csv_factory
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory, n=200)
    res = await submit(client, org, source)
    assert res.status_code == 422 and "too few" in res.json()["error"]["message"]


async def test_only_two_jobs_can_be_active_at_once(
    client, signup_org, set_org_plan, csv_factory
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    codes = [(await submit(client, org, source)).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


async def test_the_gpu_seconds_quota_gates_submission(
    client, signup_org, set_org_plan, csv_factory
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    await set_org_plan(org.org_id, "enterprise", {"gpu_seconds": 0})
    res = await submit(client, org, source)
    assert res.status_code == 429 and res.json()["error"]["code"] == "quota_exceeded"


async def test_an_api_key_cannot_submit_jobs(client, signup_org, set_org_plan, csv_factory) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    res = await client.post(
        "/v1/finetune/jobs", json={"data_source_id": source, "symbols": ["MYCO"]}, headers=org.key
    )
    assert res.status_code == 403


# ----------------------------------------------------------------- run and the gate blocks
async def test_a_job_runs_and_a_noise_model_is_rejected_by_the_gate(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    job = (await submit(client, org, source)).json()
    assert await run_to_completion(cp, tiny_base, org) is True

    done = (await client.get(f"/v1/finetune/jobs/{job['id']}", headers=org.session)).json()
    assert done["status"] == "succeeded" and done["error"] is None and done["model_id"]
    models = (await client.get("/v1/models", headers=org.session)).json()
    assert len(models) == 1 and models[0]["status"] == "rejected"
    gate = models[0]["gate"]
    assert (
        gate["passed"] is False
        and gate["reasons"]
        and gate["baseline"]["model_id"] == "random-walk"
    )
    assert gate["n_origins"] >= 30 and "artifact_key" not in models[0]
    assert models[0]["metrics"]["steps"] >= 1 and models[0]["metrics"]["device"] == "cpu"
    actions = {e["action"] for e in await store.list_audit(cp.db, UUID(org.org_id))}
    assert {"finetune.submit", "model.rejected"} <= actions


async def test_a_rejected_model_cannot_be_served_or_routed(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    await submit(client, org, source)
    await run_to_completion(cp, tiny_base, org)
    model = (await client.get("/v1/models", headers=org.session)).json()[0]
    use = await client.post(
        "/v1/forecast", json={**FORECAST, "model": f"ft:{model['id']}"}, headers=org.key
    )
    assert use.status_code == 400 and use.json()["error"]["code"] == "model_unavailable"
    assert "rejected" in use.json()["error"]["message"]
    route = await client.put(
        "/v1/routing",
        json={"default_model": f"ft:{model['id']}", "overrides": {}},
        headers=org.session,
    )
    assert route.status_code == 422


async def test_the_database_itself_refuses_to_promote_a_model_that_failed_its_gate(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    await submit(client, org, source)
    await run_to_completion(cp, tiny_base, org)
    model = (await client.get("/v1/models", headers=org.session)).json()[0]
    async with cp.db.tenant(UUID(org.org_id)) as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "UPDATE models SET status = 'promoted' WHERE id = $1::uuid", model["id"]
            )


async def test_usage_is_metered_in_gpu_seconds_for_the_job(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    job = (await submit(client, org, source)).json()
    await run_to_completion(cp, tiny_base, org)
    rows = (await client.get("/v1/usage", headers=org.session)).json()["meters"]
    gpu = next(r for r in rows if r["kind"] == "gpu_seconds")
    finished = (await client.get(f"/v1/finetune/jobs/{job['id']}", headers=org.session)).json()
    assert gpu["used"] >= 1 and abs(gpu["used"] - finished["gpu_seconds"]) <= 1.5


# ------------------------------------------------------------- promoted: serving + routing
async def promoted_model(client, org, source, cp, tiny_base, monkeypatch) -> str:
    passing_gate(monkeypatch)
    await submit(client, org, source)
    await run_to_completion(cp, tiny_base, org)
    model = (await client.get("/v1/models", headers=org.session)).json()[0]
    assert model["status"] == "promoted" and model["promoted_at"]
    return str(model["id"])


async def test_a_promoted_model_is_served_metered_and_labelled(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    model = await promoted_model(client, org, source, cp, tiny_base, monkeypatch)
    res = await client.post(
        "/v1/forecast", json={**FORECAST, "model": f"ft:{model}"}, headers=org.key
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert f"ft:{model}" in body["model_id"] and body["calibration_status"] == "uncalibrated"
    assert "Not investment advice" in body["disclaimer"] and body["model_card"]
    usage = (await client.get("/v1/usage", headers=org.session)).json()["meters"]
    assert next(r for r in usage if r["kind"] == "forecast_calls")["used"] == 1


async def test_routing_picks_the_model_per_symbol(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    await org.upload("OTHER", csv_factory(n=720, seed=9), source)
    model = await promoted_model(client, org, source, cp, tiny_base, monkeypatch)
    put = await client.put(
        "/v1/routing",
        json={"default_model": f"ft:{model}", "overrides": {"OTHER": "random-walk"}},
        headers=org.session,
    )
    assert put.status_code == 200
    assert (await client.get("/v1/routing", headers=org.session)).json()[
        "default_model"
    ] == f"ft:{model}"
    mine = (
        await client.post("/v1/forecast", json={**FORECAST, "model": "routed"}, headers=org.key)
    ).json()
    other = (
        await client.post(
            "/v1/forecast", json={**FORECAST, "symbol": "OTHER", "model": "routed"}, headers=org.key
        )
    ).json()
    assert f"ft:{model}" in mine["model_id"] and "random-walk" in other["model_id"]


async def test_routing_rejects_unknown_and_foreign_models(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    a, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    b, _ = await ready_org(client, signup_org, set_org_plan, csv_factory)
    model = await promoted_model(client, a, source, cp, tiny_base, monkeypatch)
    for target in (f"ft:{model}", "ft:00000000-0000-0000-0000-000000000000", "gpt-9", "ft:../etc"):
        res = await client.put(
            "/v1/routing", json={"default_model": target, "overrides": {}}, headers=b.session
        )
        assert res.status_code == 422, target
    bad_symbol = await client.put(
        "/v1/routing",
        json={"default_model": "random-walk", "overrides": {"../x": "random-walk"}},
        headers=a.session,
    )
    assert bad_symbol.status_code == 422


async def test_one_tenants_model_is_invisible_and_unusable_to_another(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    a, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    b, _ = await ready_org(client, signup_org, set_org_plan, csv_factory)
    model = await promoted_model(client, a, source, cp, tiny_base, monkeypatch)
    assert (await client.get("/v1/models", headers=b.session)).json() == []
    assert (await client.get(f"/v1/models/{model}", headers=b.session)).status_code == 404
    assert (await client.post(f"/v1/models/{model}/archive", headers=b.session)).status_code == 404
    stolen = await client.post(
        "/v1/forecast", json={**FORECAST, "model": f"ft:{model}"}, headers=b.key
    )
    assert stolen.status_code == 400 and stolen.json()["error"]["code"] == "model_unavailable"
    assert (await client.get("/v1/finetune/jobs", headers=b.session)).json() == []
    mine = (await client.get("/v1/models", headers=a.session)).json()
    assert mine[0]["id"] == model  # and the owner still has it


async def test_a_tampered_artifact_is_refused_before_it_is_loaded(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    model = await promoted_model(client, org, source, cp, tiny_base, monkeypatch)
    files = list(cp.blobs.root.rglob("model.safetensors"))
    assert len(files) >= 1
    target = next(f for f in files if model in f.parts)
    target.write_bytes(target.read_bytes()[:-8] + b"tampered")
    cp.gateway.private_models._cache.clear()
    res = await client.post(
        "/v1/forecast", json={**FORECAST, "model": f"ft:{model}"}, headers=org.key
    )
    assert res.status_code == 400 and "integrity" in res.json()["error"]["message"]


async def test_an_archived_model_stops_being_served(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    model = await promoted_model(client, org, source, cp, tiny_base, monkeypatch)
    assert (
        await client.post(f"/v1/models/{model}/archive", headers=org.session)
    ).status_code == 204
    cp.gateway.private_models._cache.clear()
    res = await client.post(
        "/v1/forecast", json={**FORECAST, "model": f"ft:{model}"}, headers=org.key
    )
    assert res.status_code == 400 and "archived" in res.json()["error"]["message"]


async def test_private_model_names_need_a_resolver(client, signup_org, cp) -> None:
    org = await signup_org()
    await org.make_key()
    cp.gateway.private_models = None
    res = await client.post(
        "/v1/forecast", json={**FORECAST, "symbol": "SYN-GBM", "model": "routed"}, headers=org.key
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "model_unavailable"


# ----------------------------------------------------------------------- jobs and workers
async def test_a_queued_job_can_be_cancelled_and_a_finished_one_cannot(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    first = (await submit(client, org, source)).json()
    cancelled = await client.post(f"/v1/finetune/jobs/{first['id']}/cancel", headers=org.session)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert await run_to_completion(cp, tiny_base, org) is False  # nothing left to run
    again = await client.post(f"/v1/finetune/jobs/{first['id']}/cancel", headers=org.session)
    assert again.status_code == 409


async def test_other_tenants_cannot_see_or_cancel_a_job(
    client, signup_org, set_org_plan, csv_factory
) -> None:
    a, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    b, _ = await ready_org(client, signup_org, set_org_plan, csv_factory)
    job = (await submit(client, a, source)).json()
    assert (
        await client.get(f"/v1/finetune/jobs/{job['id']}", headers=b.session)
    ).status_code == 404
    assert (
        await client.post(f"/v1/finetune/jobs/{job['id']}/cancel", headers=b.session)
    ).status_code == 404


async def test_two_workers_never_take_the_same_job(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    await submit(client, org, source)
    pool = f"dedicated-{org.org_id}"
    w1, w2 = context(cp, tiny_base), context(cp, tiny_base)
    got = await asyncio.gather(claim(w1, pool), claim(w2, pool))
    assert sum(1 for g in got if g is not None) == 1


async def test_a_worker_only_serves_its_own_pool(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    await submit(client, org, source)
    assert await claim(context(cp, tiny_base), "some-other-pool") is None


async def test_a_job_whose_data_has_gone_fails_cleanly_and_is_still_metered(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base
) -> None:
    org, source = await ready_org(client, signup_org, set_org_plan, csv_factory)
    job = (await submit(client, org, source)).json()
    cp.blobs.delete_prefix(f"orgs/{org.org_id}/ds/{source}")
    await run_to_completion(cp, tiny_base, org)
    failed = (await client.get(f"/v1/finetune/jobs/{job['id']}", headers=org.session)).json()
    assert failed["status"] == "failed" and failed["error"]
    assert "Traceback" not in failed["error"] and len(failed["error"]) < 400
    assert (await client.get("/v1/models", headers=org.session)).json() == []


async def test_the_worker_reads_only_the_jobs_own_tenants_data(
    client, signup_org, set_org_plan, csv_factory, cp, tiny_base, monkeypatch
) -> None:
    """Two tenants upload different series under the same symbol; each model trains on its own."""
    seen: list[tuple[str, float]] = []
    original = worker._load_series

    def spy(ctx, org_id, source_id, symbols, as_of):
        out = original(ctx, org_id, source_id, symbols, as_of)
        seen.append((str(org_id), float(out[symbols[0]]["close"].iloc[-1])))
        return out

    monkeypatch.setattr(worker, "_load_series", spy)
    a, sa = await ready_org(client, signup_org, set_org_plan, csv_factory, n=720)
    b = await signup_org()
    await set_org_plan(b.org_id, "enterprise")
    info_b = await b.upload("MYCO", csv_factory(n=720, seed=5, base=9000.0))
    await submit(client, a, sa)
    await submit(client, b, info_b["source_id"])
    await run_to_completion(cp, tiny_base, a)
    await run_to_completion(cp, tiny_base, b)
    by_org = dict(seen)
    assert 10 < by_org[a.org_id] < 2000 and by_org[b.org_id] > 2000


# --------------------------------------------------------------------------- the trainer
def _windows(csv_factory):
    import io

    import pandas as pd

    from tycheon.data.schema import normalize_bars

    frame = pd.read_csv(io.StringIO(csv_factory(n=720)), parse_dates=["timestamp"]).set_index(
        "timestamp"
    )
    frame.index = frame.index.tz_localize("UTC")
    bars = normalize_bars(frame, bar_duration=pd.Timedelta(days=1))
    cfg = FineTuneConfig(symbols=["AAA"], **SMALL)
    train, val, _, _ = build_windows({"AAA": bars}, cfg)
    return cfg, train, val


def test_training_is_deterministic_for_a_seed(csv_factory, tiny_base) -> None:
    cfg, train, val = _windows(csv_factory)
    a = fine_tune(tiny_base, train, val, cfg, device_request="cpu")
    b = fine_tune(tiny_base, train, val, cfg, device_request="cpu")
    assert a.steps == b.steps == cfg.max_steps and a.train_loss == b.train_loss
    assert all(math.isfinite(x) and abs(x) < 1e6 for x in a.train_loss)  # finite
    assert a.device == "cpu" and a.val_loss


def test_training_changes_the_weights_but_not_the_base(csv_factory, tiny_base) -> None:
    import torch

    cfg, train, val = _windows(csv_factory)
    before = {k: v.clone() for k, v in tiny_base.load()[1].state_dict().items()}
    trained = fine_tune(tiny_base, train, val, cfg, device_request="cpu")
    after = trained.model.state_dict()
    assert any(not torch.equal(before[k], after[k]) for k in before)
    fresh = tiny_base.load()[1].state_dict()
    assert all(torch.equal(before[k], fresh[k]) for k in before)  # the base was never touched


def test_a_cancelled_job_stops_training(csv_factory, tiny_base) -> None:
    cfg, train, val = _windows(csv_factory)
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(TrainingCancelledError):
        fine_tune(tiny_base, train, val, cfg, device_request="cpu", should_stop=stop)


def test_registry_rejects_an_unknown_routing_shape() -> None:
    assert registry.parse_ft("ft:00000000-0000-0000-0000-000000000001") is not None
    assert registry.parse_ft("ft:not-a-uuid") is None and registry.parse_ft("routed") is None
    assert json.dumps({"default_model": "random-walk"})
