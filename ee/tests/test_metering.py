"""Metering accuracy and quota enforcement against a real Postgres.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tycheon_cp.metering import (
    FeatureNotIncludedError,
    Meter,
    QuotaExceededError,
    clean_idempotency_key,
    current_period,
)
from tycheon_cp.plans import load_plans
from tycheon_cp.testing import make_org

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

PLANS = load_plans()
DEV = PLANS.get("developer")


class Clock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def __call__(self) -> datetime:
        return self.when


async def counter(db, org, kind):
    async with db.tenant(org.org_id) as conn:
        value = await conn.fetchval("SELECT used FROM usage_counters WHERE kind = $1", kind)
    return int(value or 0)


async def events(db, org, kind):
    async with db.tenant(org.org_id) as conn:
        value = await conn.fetchval(
            "SELECT coalesce(sum(quantity), 0) FROM usage_events WHERE kind = $1", kind
        )
    return int(value)


async def call(meter, org, plan=DEV, kind="forecast_calls", *, fail=False, override=None):
    """One metered call, the way the API makes it."""
    reservation = await meter.reserve(org.org_id, plan, kind, override=override)
    if fail:
        await meter.release(reservation)
        return False
    await meter.commit(reservation, idempotency_key=uuid.uuid4().hex)
    return True


async def test_n_successful_calls_are_metered_as_exactly_n(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    for _ in range(37):
        await call(meter, org)
    assert await counter(db, org, "forecast_calls") == 37
    assert await events(db, org, "forecast_calls") == 37


async def test_failed_calls_are_never_billed(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    for i in range(20):
        await call(meter, org, fail=i % 4 != 0)  # 5 succeed, 15 fail
    assert await counter(db, org, "forecast_calls") == 5
    assert await events(db, org, "forecast_calls") == 5


async def test_the_counter_always_equals_the_sum_of_recorded_events(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    results = await asyncio.gather(*[call(meter, org, fail=i % 3 == 0) for i in range(60)])
    assert await counter(db, org, "forecast_calls") == sum(results)
    assert await events(db, org, "forecast_calls") == sum(results)


async def test_a_quota_is_a_hard_limit_even_under_concurrency(db) -> None:
    """50 simultaneous calls against a limit of 10: exactly 10 get through, never 11."""
    org, meter = await make_org(db), Meter(db, PLANS)
    outcomes = await asyncio.gather(
        *[
            meter.reserve(org.org_id, DEV, "forecast_calls", override={"forecast_calls": 10})
            for _ in range(50)
        ],
        return_exceptions=True,
    )
    granted = [o for o in outcomes if not isinstance(o, Exception)]
    refused = [o for o in outcomes if isinstance(o, QuotaExceededError)]
    assert len(granted) == 10 and len(refused) == 40
    assert await counter(db, org, "forecast_calls") == 10


async def test_the_free_plan_stops_at_its_documented_limit(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    assert DEV.limit("forecast_calls") == 1000
    small = {"forecast_calls": 3}
    for _ in range(3):
        await call(meter, org, override=small)
    with pytest.raises(QuotaExceededError) as raised:
        await call(meter, org, override=small)
    assert raised.value.limit == 3 and raised.value.used == 3


async def test_a_request_larger_than_the_whole_limit_is_refused_outright(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    with pytest.raises(QuotaExceededError):
        await meter.reserve(org.org_id, DEV, "forecast_calls", 5, override={"forecast_calls": 3})
    assert await counter(db, org, "forecast_calls") == 0


async def test_retrying_with_the_same_idempotency_key_is_recorded_once(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    key = "retry-key-0001"
    first = await meter.reserve(org.org_id, DEV, "forecast_calls")
    second = await meter.reserve(org.org_id, DEV, "forecast_calls")
    assert await meter.commit(first, idempotency_key=key) is True
    assert await meter.commit(second, idempotency_key=key) is False  # the duplicate is refunded
    assert await counter(db, org, "forecast_calls") == 1
    assert await events(db, org, "forecast_calls") == 1


async def test_the_month_rolls_over_and_each_month_has_its_own_counter(db) -> None:
    org = await make_org(db)
    clock = Clock(datetime(2026, 1, 31, 23, 59, tzinfo=UTC))
    meter = Meter(db, PLANS, clock=clock)
    override = {"forecast_calls": 2}
    await call(meter, org, override=override)
    await call(meter, org, override=override)
    with pytest.raises(QuotaExceededError):
        await call(meter, org, override=override)
    clock.when = datetime(2026, 2, 1, 0, 1, tzinfo=UTC)
    await call(meter, org, override=override)  # a new month, a fresh allowance
    summary = {r["kind"]: r for r in await meter.summary(org.org_id, DEV, override=override)}
    assert (
        summary["forecast_calls"]["used"] == 1
        and summary["forecast_calls"]["period"] == "2026-02-01"
    )


async def test_usage_is_counted_per_organisation_not_shared(db) -> None:
    a, b, meter = await make_org(db), await make_org(db), Meter(db, PLANS)
    for _ in range(4):
        await call(meter, a)
    await call(meter, b)
    assert await counter(db, a, "forecast_calls") == 4
    assert await counter(db, b, "forecast_calls") == 1


async def test_compute_meters_record_exact_seconds_and_gate_on_headroom(db) -> None:
    org, meter = await make_org(db), Meter(db, PLANS)
    override = {"backtest_compute_seconds": 10}
    await meter.ensure_headroom(org.org_id, DEV, "backtest_compute_seconds", override=override)
    assert await meter.record_actual(
        org.org_id, "backtest_compute_seconds", 12, idempotency_key="bt-0000001"
    )
    assert not await meter.record_actual(
        org.org_id, "backtest_compute_seconds", 12, idempotency_key="bt-0000001"
    )
    assert await counter(db, org, "backtest_compute_seconds") == 12  # may overshoot by one job
    with pytest.raises(QuotaExceededError):
        await meter.ensure_headroom(org.org_id, DEV, "backtest_compute_seconds", override=override)


async def test_a_feature_not_in_the_plan_is_refused(db) -> None:
    meter = Meter(db, PLANS)
    with pytest.raises(FeatureNotIncludedError):
        meter.require_feature(DEV, "mcp")
    meter.require_feature(PLANS.get("startup"), "mcp")
    meter.require_feature(PLANS.get("enterprise"), "finetune")
    with pytest.raises(FeatureNotIncludedError):
        meter.require_feature(PLANS.get("startup"), "finetune")


def test_an_idempotency_key_is_only_trusted_when_well_formed() -> None:
    assert clean_idempotency_key("abcdefgh-1234") == "abcdefgh-1234"
    for bad in (None, "", "short", "has space in it!", "x" * 200, "a;DROP TABLE"):
        assert clean_idempotency_key(bad) != bad
        assert len(clean_idempotency_key(bad)) == 32


def test_the_period_is_the_first_of_the_utc_month() -> None:
    assert current_period(datetime(2026, 3, 31, 23, 59, tzinfo=UTC)).isoformat() == "2026-03-01"
