# `tycheon.agents`: the governed agentic risk review

Pure Tycheon code: **nothing here imports Keelgate.** The agents act only through a `ToolCaller`
that `tycheon.governance` implements over Keelgate's gateway (capability grant, policy, approval,
audit), so whatever an agent does, the harness decides whether it may.

```
Planner -> specialists (governed tool calls) -> composer drafts -> independent verifier
        -> (rejected: revise, within budget) -> verified report -> save -> paper trade *proposal*
```

| Module | Role |
|---|---|
| `plan.py` | The **Planner**: a typed, budgeted `ResearchPlan`. A model proposes it; deterministic code validates and clamps it. The goal, `as_of`, portfolio and horizon are the request's, never the model's. Budget fields are clamped to hard limits. |
| `specialists.py` | `ForecastAgent`, `RiskAgent`, `NewsAgent`, `FundamentalsAgent`, `BacktestAgent`, `TradeProposer`. Each has its own least-privilege grant and maps a plan step to one governed tool call. A refusal becomes a reported evidence gap, never a guess. |
| `verifier.py` | The **independent verifier**: code, not a model, sharing nothing with the composer. Checks every number against the evidence the sentence cites, that citations exist and are not dated after `as_of`, that uncalibrated outputs are called uncalibrated, that hostile evidence is disclosed, the disclaimer, and no advice language. |
| `composer.py` | The draft prompt (evidence digest only: ids and numbers, never document text) and the final `Report` with evidence links, gaps, verifier trace and pending approvals. A report no draft could verify is **withheld**. |
| `workflow.py` | `RiskReviewWorkflow` ties it together and records a trace. |
| `models.py` | `Evidence` and `EvidenceBook`: the only numbers a report may cite come from tool results. |
| `demo.py` | Deterministic stand-in model for CI and the offline example. |

## Guarantees (each has a test)

- A model never calls a tool and never chooses the date, the portfolio or the limits.
- A draft the verifier does not accept is never published or acted on, and no trade is proposed
  from an unverified analysis.
- A paper trade can only be *proposed*: the policy resolves it to a human approval request.
- External text (news) is untrusted: only numeric scores leave it, and no document text reaches a
  prompt.
