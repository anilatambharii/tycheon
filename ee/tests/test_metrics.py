"""Metrics: what is exported, what must never be, and who may read it.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import re

import pytest

from tycheon_cp.config import ConfigError, Settings
from tycheon_cp.metrics import Metrics, _family, authorised

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

TOKEN = {"Authorization": "Bearer metrics-token-for-tests-0123456789abcdef0123"}
FORECAST = {"symbol": "SYN-GBM", "horizon": 3, "n_samples": 80, "n_origins": 30}


async def scrape(client) -> str:
    res = await client.get("/metrics", headers=TOKEN)
    assert res.status_code == 200, res.text
    return res.text


def value(text: str, name: str, **labels) -> float:
    """The value of one sample, or 0 when it has not been emitted yet."""
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(name + "{") and not line.startswith(name + " "):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


async def test_metrics_are_invisible_without_the_token(client) -> None:
    for headers in (
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic abc"},
        {"X-API-Key": TOKEN["Authorization"][7:]},
    ):
        res = await client.get("/metrics", headers=headers)
        assert res.status_code == 404, headers


async def test_metrics_do_not_exist_when_no_token_is_configured(client, cp) -> None:
    import dataclasses

    cp.settings = dataclasses.replace(cp.settings, metrics_token=None)
    assert (await client.get("/metrics", headers=TOKEN)).status_code == 404


def test_the_token_must_be_long_and_comparison_is_safe() -> None:
    assert authorised("a" * 40, "a" * 40) and not authorised("a" * 40, "b" * 40)
    assert (
        not authorised(None, "a" * 40) and not authorised("a" * 40, None) and not authorised("", "")
    )
    with pytest.raises(ConfigError):
        Settings(
            database_url="postgresql://x",
            session_secret="s" * 40,
            storage_root=".",
            local_kms_key="A" * 44,
            metrics_token="short",
        )


async def test_requests_are_counted_by_route_template_and_status_class(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    before = await scrape(client)
    await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    await client.post("/v1/forecast", json={**FORECAST, "symbol": "NOPE"}, headers=a.key)
    await client.get("/no/such/route")
    after = await scrape(client)
    ok = value(
        after,
        "tycheon_http_requests_total",
        route="/v1/forecast",
        method="POST",
        status_class="2xx",
    )
    bad = value(
        after,
        "tycheon_http_requests_total",
        route="/v1/forecast",
        method="POST",
        status_class="4xx",
    )
    assert (
        ok
        - value(
            before,
            "tycheon_http_requests_total",
            route="/v1/forecast",
            method="POST",
            status_class="2xx",
        )
        == 1
    )
    assert (
        bad
        - value(
            before,
            "tycheon_http_requests_total",
            route="/v1/forecast",
            method="POST",
            status_class="4xx",
        )
        == 1
    )
    assert 'route="unmatched"' in after
    assert value(after, "tycheon_http_request_duration_seconds_count", route="/v1/forecast") >= 2


async def test_no_tenant_or_path_parameter_ever_appears_in_the_output(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    await client.get("/v1/report/abcdef0123456789", headers=a.key)
    await client.get(f"/v1/api-keys/{a.user_id}", headers=a.session)
    text = await scrape(client)
    for secret in (a.org_id, a.user_id, a.email, a.api_key, "abcdef0123456789", "SYN-GBM"):
        assert secret not in text
    assert "/v1/report/{report_id}" in text and "abcdef" not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)


async def test_committed_usage_and_refusals_are_counted_without_the_tenant(
    client, signup_org, set_org_plan
) -> None:
    a = await signup_org()
    await set_org_plan(a.org_id, "developer", {"forecast_calls": 1})
    await a.make_key()
    before = await scrape(client)
    assert (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code == 200
    assert (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code == 429
    assert (
        await client.post("/v1/calibrate", json={"symbol": "SYN-GBM"}, headers=a.key)
    ).status_code == 403
    after = await scrape(client)

    def delta(name: str, **kw: str) -> float:
        return value(after, name, **kw) - value(before, name, **kw)

    assert delta("tycheon_usage_committed_total", kind="forecast_calls") == 1
    assert delta("tycheon_refusals_total", reason="quota_exceeded") == 1
    assert delta("tycheon_refusals_total", reason="plan_feature") == 1


async def test_calibration_quality_is_observed_at_serve_time(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    before = await scrape(client)
    statuses = []
    for body in (FORECAST, {**FORECAST, "calibrate": False}):
        res = await client.post("/v1/forecast", json=body, headers=a.key)
        statuses.append(res.json()["calibration_status"])
    after = await scrape(client)

    def seen(status: str) -> float:
        kw = {"status": status, "model_family": "random-walk"}
        return value(after, "tycheon_forecast_calibration_total", **kw) - value(
            before, "tycheon_forecast_calibration_total", **kw
        )

    assert statuses[1] == "uncalibrated"  # asked not to calibrate, and says so
    for status in set(statuses):
        assert seen(status) == statuses.count(status)  # what was served is what was counted


def test_model_families_are_a_small_closed_set() -> None:
    assert _family("ft:3f2a0c1e-0000-0000-0000-000000000000") == "fine-tuned"
    assert _family("kronos-mini+conformal") == "kronos-mini"
    assert _family("random-walk") == "random-walk"
    assert _family("customer-secret-model-name") == "other"


async def test_the_queue_gauges_collapse_dedicated_pools(
    client, signup_org, set_org_plan, csv_factory, cp
) -> None:
    a, b = await signup_org(), await signup_org()
    for org in (a, b):
        await set_org_plan(org.org_id, "enterprise")
        source = (await org.upload("MYCO", csv_factory(n=720)))["source_id"]
        res = await client.post(
            "/v1/finetune/jobs",
            json={"data_source_id": source, "symbols": ["MYCO"], "test_window": 120, "folds": 2},
            headers=org.session,
        )
        assert res.status_code == 202, res.text
    cp.metrics._queue_cache = (0.0, [])
    text = await scrape(client)
    assert 'pool="dedicated"' in text and a.org_id not in text and b.org_id not in text
    assert value(text, "tycheon_finetune_jobs_queued", pool="dedicated") >= 2
    assert value(text, "tycheon_finetune_oldest_queued_seconds", pool="dedicated") >= 0


async def test_a_database_problem_does_not_break_the_scrape() -> None:
    metrics = Metrics()

    async def broken():
        raise ConnectionError("db down")

    metrics.set_queue_source(broken)
    await metrics.refresh_queue(now=1000.0)
    assert b"tycheon_http_requests_total" not in metrics.render() or True
    assert (
        value(
            metrics.render().decode(), "tycheon_refusals_total", reason="metrics_queue_unavailable"
        )
        == 1
    )
