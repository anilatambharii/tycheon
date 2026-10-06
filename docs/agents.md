# The agentic risk review

**For research and risk analytics. Not investment advice.**

`examples/agentic_risk_review.py` runs a governed review end to end, paper only, offline:

```bash
uv sync --extra agents --extra report
uv run python examples/agentic_risk_review.py
```

```
Planner -> specialists (governed tool calls) -> composer drafts -> independent verifier
        -> (rejected: revise, within budget) -> verified report -> save -> paper trade *proposal*
```

## The roles

| Role | What it is | What it cannot do |
|---|---|---|
| **Planner** | A model proposes a typed `ResearchPlan` (steps, budget, optional trade). Deterministic code validates and clamps it. | Change the goal, the date, the portfolio, the horizon, or exceed the hard budget limits. Add a step for a symbol you do not hold. |
| **ForecastAgent, RiskAgent, NewsAgent, FundamentalsAgent, BacktestAgent** | Deterministic code. Each maps a plan step to one governed tool call under its own grant and records the result as evidence. | Use a tool outside its grant. A refusal becomes a reported *evidence gap*, never a guess. |
| **ReportComposer** | A model drafts text from the **evidence digest** (ids and numbers only); code assembles the final report with evidence links, gaps, the verifier trace and approvals. | See document text. Publish a draft the verifier did not accept. |
| **Verifier** | Independent code, sharing nothing with the composer. | Authorise an action. It can only accept, ask for a revision, or reject. |
| **TradeProposer** | Asks the governed `propose_paper_trade` tool. | Execute: the policy resolves every proposal to a human approval. |

A report no draft could verify is **withheld**: nothing is saved, no text is published and no
trade is proposed.

## What the verifier checks

1. **Every number is supported**: to its displayed precision, by a number in the evidence that
   *the same sentence cites* (or a fact of the request, like the horizon). A number found only in
   other evidence is reported as such.
2. **Citations exist and are pre-`as_of`.**
3. **Uncalibrated outputs are called uncalibrated**, and nothing uncalibrated is called calibrated.
   (A multi-asset portfolio's risk is always `uncalibrated`: dependence is assumed.)
4. **Hostile or fragile evidence is disclosed**: instruction-like news documents that were excluded,
   and risk tails estimated from too few paths.
5. The exact disclaimer is present, and there is no advice or guarantee language.

Rejection reasons are specific (`4.34% does not match any number in E10`) and are fed back to the
composer for the revision. The default budget allows two revisions.

## Evals

The evals are the tests in `tests/test_agentic_review.py` (marker `eval`; run `make evals`). They
use the real Keelgate gateway with `keelgate.testing.FakeLLM`, so CI needs no key or network:

- **Trajectory**: exactly which tools run, in order, under which agent, and that every call is in
  the audit chain.
- **Planted errors**: nine kinds of mistake (wrong number, wrong citation, uncited number, hidden
  uncalibrated status, "calibrated" claimed for uncalibrated evidence, hidden unreliable tail,
  advice language, missing disclaimer, unknown citation) are each caught on real evidence, and a
  future-dated citation is rejected.
- **Injection**: malicious news documents (instructions to trade, to hide risk, in a fake system
  tag) leave the trajectory, the trade and the sentiment unchanged, are disclosed in the report,
  and never appear in any prompt sent to the model.

Keelgate's eval framework is not implemented yet; see [governance](governance.md#differences-between-the-plan-and-keelgate-as-it-is-today).

## Running with a real model

```bash
uv run python examples/agentic_risk_review.py --llm anthropic --model <model-id>
uv run python examples/agentic_risk_review.py --llm ollama --model <model-id>
```

The provider key comes from the environment (for example `ANTHROPIC_API_KEY`). A real model may be
rejected by the verifier more than once, and may run out of its revision budget: then the report
is withheld, which is the system working as designed. This path was not exercised against a live
provider in CI.
