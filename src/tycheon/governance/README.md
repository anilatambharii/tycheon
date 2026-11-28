# `tycheon.governance`: the one place Tycheon touches Keelgate

[Keelgate](https://github.com/anilatambharii/keelgate) is the safety harness that governs
anything with a side effect: capability-scoped tools, a deterministic policy gate, human
approvals, a tamper-evident audit chain, and durable budgeted loops. Tycheon depends on it as a
library, and **every `keelgate` import lives in this package**. `tests/test_architecture.py`
enforces that, so a change to Keelgate's integration contract touches exactly one place.

## What is here

| Module | What it does |
|---|---|
| `_runtime.py` | Assembles Keelgate's gateway (registry, grant verifier, audit chain, approval queue, budget ledger, idempotency store, policy). Issues one **least-privilege grant per agent** and hands the agents a `ToolCaller`. |
| `_tools.py` | The governed tools: forecast, calibration, risk, backtest, news, fundamentals (all `READ`), `save_report` and `propose_paper_trade` (`WRITE`). Tool bodies read `as_of`, tenant and data from a trusted context, never from their arguments. |
| `_policy.py` | Keelgate's `finance_basic` pack plus a small fail-closed overlay: analytics are read-only, and a paper trade is **never** auto-approved. |
| `_loop.py` | The draft, verify, revise cycle on Keelgate's `Loop` (budgets, revision limit, checkpoints, audit). |
| `_llm.py` | Adapts Keelgate's `LLMClient` (real providers, or the scripted `FakeLLM` in CI) to the agents' `TextModel`. |
| `_serving.py` | The MCP server (stdio only) and the approvals REST app, over the same runtime. |
| `_outcome_metrics.py` | Adapts Tycheon's metrics to Keelgate's `OutcomeMetric` protocol; the `keelgate.outcome_metrics` entry points name these classes. |
| `_telemetry.py` | OpenTelemetry spans following the GenAI conventions. `keelgate.telemetry` is not adopted yet. |

## Rules this package lives under

- **It fails closed. There is no bypass.** Nothing here falls back to running a tool when Keelgate
  is missing or errors; a missing Keelgate makes the import fail. There is no `try/except
  ImportError` around a Keelgate import (a test checks).
- Only Keelgate's documented integration contract is used. Never `keelgate._internal`.
- Paper and simulation only. The policy denies any other execution mode, and Keelgate's gateway
  refuses it independently.
- External text (news, filings) is untrusted data. It never reaches a prompt and never becomes an
  instruction; see `tycheon.services.news`.
- Everything outside this package reaches an analytic or a side effect only by calling a governed
  tool, never the raw service function (a test checks that too).

## What is not adopted yet

Agent evals run as Tycheon's own pytest suites, and tracing uses the OpenTelemetry API directly,
although Keelgate 0.2.0 has `keelgate.evals` and `keelgate.telemetry`. See `docs/governance.md`.
