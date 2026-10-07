"""A governed agentic risk review, end to end, paper only.

    uv sync --extra agents --extra report
    uv run python examples/agentic_risk_review.py                        # scripted model, offline
    uv run python examples/agentic_risk_review.py --llm anthropic --model <model-id>
    uv run python examples/agentic_risk_review.py --llm ollama --model <model-id>

What happens, in order:

1. A **planner** model proposes a typed, budgeted research plan. The harness validates and clamps
   it: the model cannot change the portfolio, the date, or the limits.
2. **Specialist agents** (forecast, news, fundamentals, risk) gather evidence, each through
   governed tool calls under its *own* least-privilege grant. News documents are untrusted data:
   only numeric scores come out of them.
3. A **composer** model drafts the report from the evidence digest, and an **independent
   verifier** (code, not a model) checks every number against the evidence it cites. With the
   scripted model this run plants one wrong number in the first draft, so you see a rejection,
   the reasons, and a revision that passes.
4. Only a *verified* report is saved. Then a **paper trade proposal** is made. The policy never
   auto-approves one, so it becomes an **approval request** for a human; nothing executes.
5. The tamper-evident audit chain is verified.

With ``--llm scripted`` (the default) no network or key is needed and the run is deterministic.
A real model needs its provider key in the environment (for example ``ANTHROPIC_API_KEY``) and
may be rejected by the verifier more than once: that is the verifier doing its job.

Data is the bundled synthetic series. For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from tycheon.agents.demo import draft_from_prompt, plan_reply
from tycheon.agents.workflow import ReviewRequest, RiskReviewWorkflow
from tycheon.governance import (
    GovernedRuntime,
    KeelgateComposeRunner,
    RuntimeConfig,
    make_text_model,
    scripted_text_model,
)

# A Monday, 10:30 in New York: inside the policy's trading hours, so the proposal reaches a human.
AS_OF = datetime(2023, 10, 2, 14, 30, tzinfo=UTC)
PORTFOLIO = {"SYN-GBM": 400_000.0, "SYN-GARCH": 350_000.0, "SYN-REGIME": 250_000.0}
GOAL = "Review the risk of this portfolio and propose a small paper hedge for the largest holding."
TRADE = {
    "symbol": "SYN-GBM",
    "side": "sell",
    "notional": 5_000,
    "reason": "reduce the largest holding by a small amount",
}


def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")


async def main(args: argparse.Namespace) -> int:
    runtime = GovernedRuntime(RuntimeConfig(tenant_id="demo"))
    try:
        if args.llm == "scripted":
            plant = not args.no_plant_error
            model, _ = scripted_text_model(
                [
                    plan_reply(list(PORTFOLIO), trade=TRADE),
                    lambda _system, prompt: draft_from_prompt(prompt, plant_error=plant),
                    lambda _system, prompt: draft_from_prompt(prompt),
                    lambda _system, prompt: draft_from_prompt(prompt),
                ]
            )
            say("model: scripted (deterministic, offline)")
        else:
            if not args.model:
                say("--model is required with a real provider")
                return 2
            model = make_text_model(args.llm, args.model)
            say(f"model: {args.llm}/{args.model}")
        workflow = RiskReviewWorkflow(
            tools=runtime.caller(AS_OF),
            planner_model=model,
            writer_model=model,
            composer=KeelgateComposeRunner(runtime, as_of=AS_OF),
        )
        say(f"\nReview as of {AS_OF.isoformat()} (synthetic data, paper only)\n")
        result = await workflow.run(
            ReviewRequest(
                goal=GOAL,
                as_of=AS_OF,
                portfolio=PORTFOLIO,
                horizon=5,
                allow_trade_proposal=True,
                max_trade_notional=20_000,
            )
        )

        say("TRACE")
        for event in result.events:
            detail = f" {event.detail}" if event.detail else ""
            say(f"  {event.actor:<18} {event.action}{detail}")

        say("\nREPORT\n")
        say(result.report.to_markdown())

        say("APPROVAL REQUESTS")
        pending = runtime.pending_approvals()
        for item in pending:
            say(f"  {item.request_id}  {item.tool}  tier={item.tier}  status={item.status}")
            say(f"    args: {item.args}")
            say(f"    evidence: {', '.join(item.evidence_uris[:4])} ...")
        if not pending:
            say("  none")
        say(f"  paper orders executed: {len(runtime.blotter.orders('demo'))} (none until approved)")

        audit = runtime.verify_audit()
        say(f"\nAUDIT chain: {'OK' if audit.ok else 'BROKEN'} ({audit.records_checked} records)")

        rejections = sum(1 for v in result.compose.verdicts if v.decision != "ACCEPT")
        if args.llm == "scripted" and (rejections < 1 or result.report.status != "verified"):
            say("unexpected: the scripted run should show a rejection and then a verified report")
            return 1
        if not pending and result.report.status == "verified":
            say("unexpected: a verified review should have created an approval request")
            return 1
        return 0 if audit.ok else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--llm", default="scripted", choices=["scripted", "anthropic", "openai", "ollama"]
    )
    parser.add_argument("--model", default="", help="model id for a real provider")
    parser.add_argument(
        "--no-plant-error", action="store_true", help="scripted: do not plant a wrong number"
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
