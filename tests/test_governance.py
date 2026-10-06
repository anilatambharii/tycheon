"""Governance: grants, policy, approvals, audit, tenant isolation, MCP, approvals API, tracing."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from keelgate.policy import (
    Decision,
    PolicyAction,
    PolicyActor,
    PolicyContext,
    PolicyInput,
)

from tycheon.agents.protocols import EvidenceRef
from tycheon.governance import (
    AGENT_CAPABILITIES,
    AGENT_MCP,
    ALWAYS_APPROVE,
    ANALYTICS_CAPABILITIES,
    DEFAULT_LIMITS,
    ApproverToken,
    GovernanceError,
    GovernedRuntime,
    RuntimeConfig,
    TycheonPolicy,
    build_approvals_app,
    build_mcp,
    instrument,
    make_text_model,
    scripted_text_model,
)

# A Monday, 10:30 in New York: inside the pack's trading hours.
AS_OF = datetime(2023, 10, 2, 14, 30, tzinfo=UTC)
SATURDAY = datetime(2023, 9, 30, 15, 0, tzinfo=UTC)
TRADE = {"symbol": "SYN-GBM", "side": "buy", "notional": 1000, "client_order_id": "t1"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rt(tmp_path):
    runtime = GovernedRuntime(RuntimeConfig(tenant_id="acme", state_dir=tmp_path / "state"))
    yield runtime
    runtime.close()


# ------------------------------------------------------------------------------- grants
def test_every_agent_holds_only_exact_capabilities() -> None:
    """Keelgate refuses wildcards; this also checks nobody was handed a broad set by accident."""
    for agent, caps in AGENT_CAPABILITIES.items():
        assert caps, agent
        for cap in caps:
            resource, _, action = cap.partition(":")
            assert resource and action and "*" not in cap, (agent, cap)
    assert AGENT_CAPABILITIES["news-agent"] == ("news:read",)
    assert AGENT_CAPABILITIES["trade-proposer"] == ("trade:paper_execute",)
    # only the trade proposer and the MCP principal may even *propose* a trade
    holders = {a for a, caps in AGENT_CAPABILITIES.items() if "trade:paper_execute" in caps}
    assert holders == {"trade-proposer", "mcp-agent"}


def test_the_registry_has_exactly_the_documented_tools(rt) -> None:
    assert rt.tool_names() == sorted(
        [
            "forecast_distribution",
            "calibration_report",
            "portfolio_risk",
            "risk_report",
            "backtest_summary",
            "news_signals",
            "fundamentals_snapshot",
            "save_report",
            "propose_paper_trade",
        ]
    )


@pytest.mark.parametrize(
    ("agent", "tool", "args"),
    [
        ("news-agent", "forecast_distribution", {"symbol": "SYN-GBM"}),
        ("news-agent", "portfolio_risk", {"positions": {"SYN-GBM": 1.0}}),
        ("forecast-agent", "news_signals", {"symbol": "SYN-GBM"}),
        ("fundamentals-agent", "news_signals", {"symbol": "SYN-GBM"}),
        ("risk-agent", "fundamentals_snapshot", {"symbol": "SYN-GBM"}),
        ("forecast-agent", "propose_paper_trade", TRADE),
        ("news-agent", "propose_paper_trade", TRADE),
        ("risk-agent", "save_report", {"report_id": "r1", "markdown": "x"}),
        ("trade-proposer", "news_signals", {"symbol": "SYN-GBM"}),
        ("api-reader", "propose_paper_trade", TRADE),
        ("api-reader", "save_report", {"report_id": "r1", "markdown": "x"}),
        ("mcp-agent", "save_report", {"report_id": "r1", "markdown": "x"}),
    ],
)
def test_an_agent_cannot_use_a_tool_it_was_not_granted(rt, agent, tool, args) -> None:
    result = run(rt.call(agent, tool, args, as_of=AS_OF))
    assert result.status == "DENIED" and result.error_code == "capability_denied"
    assert result.output is None


@pytest.mark.parametrize(
    ("agent", "tool", "args"),
    [
        ("news-agent", "news_signals", {"symbol": "SYN-GBM"}),
        ("fundamentals-agent", "fundamentals_snapshot", {"symbol": "SYN-GBM"}),
        ("api-reader", "news_signals", {"symbol": "SYN-GARCH"}),
        ("mcp-agent", "fundamentals_snapshot", {"symbol": "SYN-REGIME"}),
    ],
)
def test_an_agent_can_use_exactly_the_tools_it_was_granted(rt, agent, tool, args) -> None:
    result = run(rt.call(agent, tool, args, as_of=AS_OF))
    assert result.status == "OK" and result.output is not None


def test_an_unknown_agent_has_no_grant(rt) -> None:
    with pytest.raises(GovernanceError) as info:
        run(rt.call("rogue-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF))
    assert info.value.code == "unknown_agent"


# ------------------------------------------------------------------- trusted as_of
def test_a_tool_call_cannot_smuggle_in_its_own_as_of(rt) -> None:
    result = run(
        rt.call(
            "news-agent",
            "news_signals",
            {"symbol": "SYN-GBM", "as_of": "2099-01-01T00:00:00+00:00"},
            as_of=AS_OF,
        )
    )
    assert result.status == "ERROR" and result.error_code == "invalid_arguments"


def test_the_harness_as_of_decides_what_a_tool_can_see(rt) -> None:
    early = datetime(2023, 9, 1, tzinfo=UTC)
    late = AS_OF
    a = run(rt.call("news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=early)).output
    b = run(rt.call("news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=late)).output
    assert a is not None and b is not None
    assert a["n_documents_used"] < b["n_documents_used"]
    assert all(s["published_at"] <= early.isoformat() for s in a["signals"])


def test_a_tool_failure_is_a_result_not_a_crash(rt) -> None:
    result = run(rt.call("news-agent", "news_signals", {"symbol": "NOPE"}, as_of=AS_OF))
    assert result.status == "OK" and result.output["n_documents_used"] == 0  # no docs, no guess
    bad = run(rt.call("forecast-agent", "forecast_distribution", {"symbol": "NOPE"}, as_of=AS_OF))
    assert bad.status == "ERROR" and bad.error_code == "tool_failed"


# ----------------------------------------------------------------------------- policy
def policy_input(
    capability: str = "trade:paper_execute",
    side: str = "WRITE",
    mode: str = "paper",
    notional: float = 1000.0,
    symbol: str = "SYN-GBM",
    as_of: datetime = AS_OF,
    exposure: float = 0.0,
) -> PolicyInput:
    return PolicyInput(
        action=PolicyAction(tool="t", side_effect=side, capability=capability, args={}),  # type: ignore[arg-type]
        actor=PolicyActor(agent_id="a", tenant_id="acme", grant_id="g"),
        resource={"symbol": symbol, "notional": notional},
        context=PolicyContext(
            as_of=as_of,
            execution_mode=mode,
            limits=dict(DEFAULT_LIMITS),
            exposure={"daily_notional": exposure},
        ),
    )


def decide(**kw):
    return run(TycheonPolicy().decide(policy_input(**kw)))


def test_a_small_paper_trade_requires_approval_not_auto_allow() -> None:
    d = decide(notional=1.0)
    assert d.effect is Decision.REQUIRE_APPROVAL and d.approval_tier.value == "ONE_CLICK"
    assert d.engine == "tycheon-policy" and d.policy_version.startswith("tycheon-overlay-1+")


def test_a_large_trade_keeps_the_stricter_tier_from_the_pack() -> None:
    d = decide(notional=30_000)
    assert d.effect is Decision.REQUIRE_APPROVAL and d.approval_tier.value == "EXPLICIT_SIGNOFF"


@pytest.mark.parametrize("mode", ["live", "production", "", "PAPER", "paper ", "sandbox"])
def test_any_non_paper_mode_is_denied(mode) -> None:
    for cap, side in (("trade:paper_execute", "WRITE"), ("forecast:run", "READ")):
        d = decide(capability=cap, side=side, mode=mode)
        assert d.effect is Decision.DENY and "live execution is forbidden" in d.reasons[0]


def test_analytics_are_allowed_only_as_read() -> None:
    for cap in ANALYTICS_CAPABILITIES:
        assert decide(capability=cap, side="READ").effect is Decision.ALLOW
        for side in ("WRITE", "PROPOSE"):
            assert decide(capability=cap, side=side).effect is Decision.DENY


@pytest.mark.parametrize("cap", ["market_data:write", "admin:all", "trade:live_execute", "x:y"])
def test_unknown_capabilities_are_denied_by_default(cap) -> None:
    assert decide(capability=cap, side="WRITE").effect is Decision.DENY


def test_the_packs_limits_still_apply_through_the_overlay() -> None:
    assert decide(notional=99_000).effect is Decision.DENY  # per-action cap
    assert decide(symbol="SYN-GBM", exposure=249_500, notional=1000).effect is Decision.DENY
    assert decide(as_of=SATURDAY).effect is Decision.DENY  # outside trading hours
    assert "outside trading hours" in decide(as_of=SATURDAY).reasons


def test_report_write_is_allowed_by_the_pack() -> None:
    assert decide(capability="report:write", side="WRITE").effect is Decision.ALLOW


def test_a_broken_inner_engine_fails_closed() -> None:
    class Broken:
        policy_version = "broken"

        async def decide(self, policy_input):
            raise RuntimeError("boom")

    d = run(TycheonPolicy(Broken()).decide(policy_input()))
    assert d.effect is Decision.DENY and d.reasons == ("policy evaluation failed",)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    notional=st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False),
    symbol=st.text(min_size=0, max_size=12),
    mode=st.sampled_from(["paper", "simulation", "live", "", "x"]),
    exposure=st.floats(min_value=-1e6, max_value=1e9, allow_nan=False, allow_infinity=False),
    day=st.integers(min_value=1, max_value=28),
    hour=st.integers(min_value=0, max_value=23),
)
def test_property_a_paper_trade_is_never_auto_allowed(
    notional, symbol, mode, exposure, day, hour
) -> None:
    """Whatever the inputs, a trade capability is DENY or REQUIRE_APPROVAL: never ALLOW."""
    for cap in ALWAYS_APPROVE:
        d = decide(
            capability=cap,
            notional=notional,
            symbol=symbol,
            mode=mode,
            exposure=exposure,
            as_of=datetime(2023, 10, day, hour, 0, tzinfo=UTC),
        )
        assert d.effect in (Decision.DENY, Decision.REQUIRE_APPROVAL)


# --------------------------------------------------------------------------- approvals
def test_a_paper_trade_waits_for_a_human_and_runs_exactly_once(rt) -> None:
    proposal = run(
        rt.call(
            "trade-proposer", "propose_paper_trade", TRADE, as_of=AS_OF,
            rationale="hedge", evidence=[EvidenceRef("evidence:E1", "forecast")],
            flags=["uncalibrated_output"],
        )
    )  # fmt: skip
    assert proposal.status == "APPROVAL_REQUIRED" and proposal.approval_id
    assert proposal.policy_effect == "REQUIRE_APPROVAL" and proposal.approval_tier == "ONE_CLICK"
    assert rt.blotter.orders("acme") == []  # nothing happened yet

    (pending,) = rt.pending_approvals()
    assert pending.request_id == proposal.approval_id and pending.tool == "propose_paper_trade"
    assert pending.evidence_uris == ("evidence:E1",) and pending.flags == ("uncalibrated_output",)
    assert pending.args["notional"] == 1000

    rt.approve(proposal.approval_id, approver_id="alice")
    done = run(
        rt.call(
            "trade-proposer",
            "propose_paper_trade",
            TRADE,
            as_of=AS_OF,
            approval_id=proposal.approval_id,
        )
    )
    assert done.status == "OK" and done.output["status"] == "filled-paper"
    assert len(rt.blotter.orders("acme")) == 1

    replay = run(  # the same approval and arguments again: a replay, never a second order
        rt.call(
            "trade-proposer",
            "propose_paper_trade",
            TRADE,
            as_of=AS_OF,
            approval_id=proposal.approval_id,
        )
    )
    assert len(rt.blotter.orders("acme")) == 1 and replay.status in ("OK", "DENIED")


def test_an_approval_cannot_be_used_for_different_arguments(rt) -> None:
    proposal = run(rt.call("trade-proposer", "propose_paper_trade", TRADE, as_of=AS_OF))
    rt.approve(proposal.approval_id, approver_id="alice")
    other = {**TRADE, "notional": 2000, "client_order_id": "t2"}
    result = run(
        rt.call(
            "trade-proposer",
            "propose_paper_trade",
            other,
            as_of=AS_OF,
            approval_id=proposal.approval_id,
        )
    )
    assert result.status != "OK" and rt.blotter.orders("acme") == []


def test_a_rejected_proposal_never_executes(rt) -> None:
    proposal = run(rt.call("trade-proposer", "propose_paper_trade", TRADE, as_of=AS_OF))
    rt.reject(proposal.approval_id, approver_id="alice", note="no")
    result = run(
        rt.call(
            "trade-proposer",
            "propose_paper_trade",
            TRADE,
            as_of=AS_OF,
            approval_id=proposal.approval_id,
        )
    )
    assert result.status != "OK" and rt.blotter.orders("acme") == []


def test_a_proposal_without_an_approval_id_cannot_run_by_retrying(rt) -> None:
    for _ in range(3):
        r = run(rt.call("trade-proposer", "propose_paper_trade", TRADE, as_of=AS_OF))
        assert r.status == "APPROVAL_REQUIRED"
    assert rt.blotter.orders("acme") == []


def test_an_explicit_signoff_needs_the_code_and_clearance(rt) -> None:
    big = {**TRADE, "notional": 30_000, "client_order_id": "big"}
    proposal = run(rt.call("trade-proposer", "propose_paper_trade", big, as_of=AS_OF))
    assert proposal.approval_tier == "EXPLICIT_SIGNOFF"
    with pytest.raises(GovernanceError):  # a one-click approver is not cleared for it
        rt.approve(proposal.approval_id, approver_id="alice", max_tier="ONE_CLICK")
    with pytest.raises(GovernanceError):  # cleared, but did not echo the evidence code
        rt.approve(proposal.approval_id, approver_id="bob", max_tier="EXPLICIT_SIGNOFF")
    (pending,) = rt.pending_approvals()
    approved = rt.approve(
        proposal.approval_id, approver_id="bob", max_tier="EXPLICIT_SIGNOFF",
        signoff_code=pending.signoff_code,
    )  # fmt: skip
    assert approved.status == "APPROVED"


def test_an_over_limit_trade_is_denied_with_no_approval_request(rt) -> None:
    r = run(
        rt.call("trade-proposer", "propose_paper_trade", {**TRADE, "notional": 99_000}, as_of=AS_OF)
    )
    assert r.status == "DENIED" and "notional exceeds the per-action limit" in r.policy_reasons
    assert rt.pending_approvals() == []


# ------------------------------------------------------------------- tenant isolation
def test_tenants_cannot_see_or_decide_each_others_requests(rt) -> None:
    mine = run(
        rt.call("trade-proposer", "propose_paper_trade", TRADE, as_of=AS_OF, tenant_id="acme")
    )
    other = run(
        rt.call(
            "trade-proposer",
            "propose_paper_trade",
            {**TRADE, "client_order_id": "z"},
            as_of=AS_OF,
            tenant_id="globex",
        )
    )
    assert [p.request_id for p in rt.pending_approvals("acme")] == [mine.approval_id]
    assert [p.request_id for p in rt.pending_approvals("globex")] == [other.approval_id]
    with pytest.raises(GovernanceError):
        rt.approve(mine.approval_id, approver_id="mallory", tenant_id="globex")
    rt.approve(other.approval_id, approver_id="gina", tenant_id="globex")
    assert [p.request_id for p in rt.pending_approvals("acme")] == [mine.approval_id]


def test_each_tenant_has_its_own_audit_chain_and_paper_book(rt) -> None:
    run(rt.call("news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF, tenant_id="acme"))
    run(
        rt.call(
            "news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF, tenant_id="globex"
        )
    )
    run(
        rt.call(
            "news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF, tenant_id="globex"
        )
    )
    a, g = rt.verify_audit("acme"), rt.verify_audit("globex")
    assert a.ok and g.ok and g.records_checked == 2 * a.records_checked
    assert rt.blotter.orders("acme") == [] and rt.blotter.orders("globex") == []


def test_a_grant_for_one_tenant_is_not_valid_in_another(rt) -> None:
    assert rt.grant_for("news-agent", "acme") != rt.grant_for("news-agent", "globex")


# ----------------------------------------------------------- long-running processes
def test_grants_are_reissued_before_they_expire_with_a_fresh_budget(tmp_path) -> None:
    import time
    from datetime import timedelta

    runtime = GovernedRuntime(
        RuntimeConfig(state_dir=tmp_path / "s", tenant_id="acme", grant_ttl=timedelta(seconds=2))
    )
    try:
        first = runtime.grant_for("news-agent", "acme")
        assert runtime.grant_for("news-agent", "acme") == first  # cached while fresh
        time.sleep(1.2)  # past half the lifetime, before expiry
        second = runtime.grant_for("news-agent", "acme")
        assert second != first
        ok = run(runtime.call("news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF))
        assert ok.status == "OK"
    finally:
        runtime.close()


def test_a_grants_spend_cap_is_enforced_and_service_principals_get_a_larger_window(
    tmp_path,
) -> None:
    runtime = GovernedRuntime(
        RuntimeConfig(
            state_dir=tmp_path / "s",
            tenant_id="acme",
            max_cost_per_grant=0.5,
            service_max_cost=50.0,
        )
    )
    try:
        # news_signals costs 0.2: two calls fit in 0.5, the third does not
        codes = [
            run(runtime.call("news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF))
            for _ in range(3)
        ]
        assert [c.status for c in codes] == ["OK", "OK", "DENIED"]
        assert codes[2].error_code == "budget_exceeded"
        for _ in range(5):  # the REST principal has a much larger window
            r = run(runtime.call("api-reader", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF))
            assert r.status == "OK"
    finally:
        runtime.close()


# ---------------------------------------------------------------------------- reports
def test_a_verified_report_is_saved_under_the_tenant_directory(rt) -> None:
    args = {"report_id": "review-1", "markdown": "# hello"}
    r = run(rt.call("report-composer", "save_report", args, as_of=AS_OF))
    assert r.status == "OK" and r.output["path"].replace("\\", "/") == "acme/review-1.md"
    saved = Path(rt.report_dir) / "acme" / "review-1.md"
    assert saved.read_text(encoding="utf-8") == "# hello"
    again = run(rt.call("report-composer", "save_report", args, as_of=AS_OF))
    assert again.status == "OK"  # an idempotent replay, not a second write


@pytest.mark.parametrize("report_id", ["../x", "a/b", "", "x" * 100, "..", "a b"])
def test_report_ids_cannot_escape_the_report_directory(rt, report_id) -> None:
    r = run(
        rt.call(
            "report-composer", "save_report", {"report_id": report_id, "markdown": "x"}, as_of=AS_OF
        )
    )
    assert r.status == "ERROR" and r.error_code == "invalid_arguments"


# ------------------------------------------------------------------------------ audit
def test_every_call_is_audited_including_refusals(rt) -> None:
    before = rt.verify_audit().records_checked
    run(
        rt.call("news-agent", "forecast_distribution", {"symbol": "SYN-GBM"}, as_of=AS_OF)
    )  # denied
    run(rt.call("news-agent", "news_signals", {"symbol": "SYN-GBM"}, as_of=AS_OF))  # allowed
    events = [e["event"] for e in rt.audit_events()]
    assert rt.verify_audit().records_checked > before and rt.verify_audit().ok
    assert "grant.rejected" in events and "tool.result" in events


# ----------------------------------------------------------------------------- MCP
def test_the_same_governed_tools_are_served_over_mcp(rt) -> None:
    from mcp import Client, types

    async def scenario() -> None:
        handle = build_mcp(rt, as_of=AS_OF, agent_id=AGENT_MCP)
        async with Client(handle.server) as client:
            listed = {t.name for t in (await client.list_tools()).tools}
            assert listed == set(rt.tool_names())
            ok = await client.call_tool("news_signals", {"symbol": "SYN-GBM"})
            body = json.loads(ok.content[0].text)
            assert not ok.is_error and body["status"] == "OK" and "untrusted_tool_output" in body
            denied = await client.call_tool("save_report", {"report_id": "r", "markdown": "x"})
            assert (
                denied.is_error
                and json.loads(denied.content[0].text)["error"]["code"] == "capability_denied"
            )
            trade = await client.call_tool("propose_paper_trade", TRADE)
            payload = json.loads(trade.content[0].text)
            assert trade.is_error and payload["status"] == "APPROVAL_REQUIRED"
            assert isinstance(types.TextContent, type)

    run(scenario())
    assert rt.blotter.orders("acme") == [] and len(rt.pending_approvals()) == 1


def test_mcp_uses_the_harness_as_of_and_ignores_one_in_the_arguments(rt) -> None:
    from mcp import Client

    async def scenario() -> dict:
        handle = build_mcp(rt, as_of=datetime(2023, 9, 1, tzinfo=UTC))
        async with Client(handle.server) as client:
            r = await client.call_tool(
                "news_signals", {"symbol": "SYN-GBM", "as_of": "2099-01-01T00:00:00Z"}
            )
            return json.loads(r.content[0].text)

    body = run(scenario())
    assert body["status"] == "ERROR" and body["error"]["code"] == "invalid_arguments"


# ------------------------------------------------------------------- approvals REST API
def test_the_approvals_api_authenticates_and_scopes_by_tenant(rt) -> None:
    import httpx

    proposal = run(
        rt.call("trade-proposer", "propose_paper_trade", TRADE, as_of=AS_OF, tenant_id="acme")
    )
    app = build_approvals_app(
        rt,
        {
            "tok-alice": ApproverToken("alice", "acme"),
            "tok-mallory": ApproverToken("mallory", "globex"),
        },
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.get("/approvals")).status_code == 401
            bad = await client.get("/approvals", headers={"Authorization": "Bearer nope"})
            assert bad.status_code == 401
            mine = await client.get("/approvals", headers={"Authorization": "Bearer tok-alice"})
            assert mine.status_code == 200 and len(mine.json()) == 1
            theirs = await client.get("/approvals", headers={"Authorization": "Bearer tok-mallory"})
            assert theirs.json() == []
            wrong = await client.post(
                f"/approvals/{proposal.approval_id}/approve",
                headers={"Authorization": "Bearer tok-mallory"},
                json={},
            )
            assert wrong.status_code in (403, 404)
            ok = await client.post(
                f"/approvals/{proposal.approval_id}/approve",
                headers={"Authorization": "Bearer tok-alice"},
                json={},
            )
            assert ok.status_code == 200

    run(scenario())


# --------------------------------------------------------------------------- tracing
def test_spans_carry_ids_and_counts_never_prompts() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    tracer = instrument("tycheon-test", exporter=exporter)
    model, _ = scripted_text_model(["a reply"], tracer=tracer)
    result = run(model.complete(system="SECRET SYSTEM PROMPT", prompt="SECRET USER PROMPT"))
    assert result.text == "a reply"
    (span,) = [s for s in exporter.get_finished_spans() if s.name.startswith("chat ")]
    attrs = dict(span.attributes or {})
    assert (
        attrs["gen_ai.operation.name"] == "chat"
        and attrs["gen_ai.request.model"] == "scripted-model"
    )
    assert attrs["gen_ai.usage.output_tokens"] >= 1
    assert "SECRET" not in json.dumps(attrs)


# ----------------------------------------------------------------------- LLM adapter
def test_a_scripted_model_replies_in_order_and_can_read_the_prompt() -> None:
    model, fake = scripted_text_model(["first", lambda system, prompt: prompt.upper()])
    assert run(model.complete(system="s", prompt="p")).text == "first"
    assert run(model.complete(system="s", prompt="hello")).text == "HELLO"
    assert fake.calls_made == 2
    with pytest.raises(AssertionError):  # running past the script is a loud failure
        run(model.complete(system="s", prompt="p"))


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown LLM provider"):
        make_text_model("skynet", "m")


def test_a_temporary_state_directory_is_removed_on_close() -> None:
    runtime = GovernedRuntime(RuntimeConfig())
    state = runtime.state_dir
    assert state.exists()
    runtime.close()
    assert not state.exists()
