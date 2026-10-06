"""The out-of-process Rego engine: parity with Keelgate's, and fail-closed behaviour."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from keelgate.policy import (
    Decision,
    PolicyAction,
    PolicyActor,
    PolicyContext,
    PolicyInput,
)

from tycheon.governance._policy import DEFAULT_LIMITS
from tycheon.governance._rego_process import WORKER, OutOfProcessRegoEngine


def request(notional: float = 1000.0, symbol: str = "SYN-GBM", mode: str = "paper") -> PolicyInput:
    return PolicyInput(
        action=PolicyAction(
            tool="t",
            side_effect="WRITE",  # type: ignore[arg-type]
            capability="trade:paper_execute",
            args={},
        ),
        actor=PolicyActor(agent_id="a", tenant_id="acme", grant_id="g"),
        resource={"symbol": symbol, "notional": notional},
        context=PolicyContext(
            as_of=datetime(2024, 3, 5, 15, tzinfo=UTC),
            execution_mode=mode,
            limits=dict(DEFAULT_LIMITS),
            exposure={"daily_notional": 0.0},
        ),
    )


@pytest.fixture
def engine():
    eng = OutOfProcessRegoEngine()
    yield eng
    eng.close()


def decide(eng, **kw):
    return asyncio.run(eng.decide(request(**kw)))


def test_it_evaluates_the_real_finance_basic_pack(engine) -> None:
    assert decide(engine, notional=1000.0).effect is Decision.ALLOW
    assert decide(engine, notional=10_000_000.0).effect is not Decision.ALLOW
    assert decide(engine, mode="live").effect is Decision.DENY


def test_it_reports_a_stable_policy_version_without_starting_a_worker(engine) -> None:
    assert len(engine.policy_version) >= 8 and engine._worker is None


def test_the_worker_is_reused_between_decisions(engine) -> None:
    decide(engine)
    first = engine._worker
    decide(engine)
    assert first is not None and engine._worker is first


def test_a_killed_worker_is_a_denial_and_the_next_call_recovers(engine) -> None:
    decide(engine)
    engine._worker._proc.kill()
    engine._worker._proc.wait()
    broken = decide(engine)
    assert broken.effect is Decision.DENY and "policy evaluation failed" in broken.reasons[0]
    assert engine._worker is None
    assert decide(engine).effect is Decision.ALLOW


def test_a_worker_that_answers_garbage_is_a_denial(engine, monkeypatch) -> None:
    decide(engine)
    monkeypatch.setattr(engine._worker, "ask", lambda document: "not json\n")
    assert decide(engine).effect is Decision.DENY
    assert engine._worker is None


def test_a_slow_worker_times_out_into_a_denial() -> None:
    eng = OutOfProcessRegoEngine(timeout=0.0001)
    try:
        assert decide(eng).effect is Decision.DENY
    finally:
        eng.close()


def test_an_unrepresentable_input_is_refused(engine) -> None:
    done = decide(engine, notional=float("nan"))
    assert done.effect is Decision.DENY


def test_the_worker_script_never_imports_duckdb_or_tycheon() -> None:
    """The point of the process boundary: duckdb and regopy must not share a process."""
    source = WORKER.read_text(encoding="utf-8")
    assert "duckdb" not in source.replace("DuckDB", "").replace("``duckdb``", "")
    assert "import tycheon" not in source and "from tycheon" not in source
