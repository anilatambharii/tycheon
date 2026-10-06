# Governance: how Tycheon uses Keelgate

**For research and risk analytics. Not investment advice.**

[Keelgate](https://github.com/anilatambharii/keelgate) is the safety harness: capability-scoped
tools, a deterministic policy gate, human approvals, a tamper-evident audit chain and durable
budgeted loops. Tycheon depends on it as a library, and **every `keelgate` import lives in
`src/tycheon/governance/`**. A test fails if anything else imports it, if anything imports
Keelgate's internals, or if a `try/except ImportError` falls back to running a tool without it.

## The rule that matters

> The LLM proposes; deterministic code decides.

A language model in Tycheon can draft a plan and draft report text. It cannot call a tool,
choose the date, pick the portfolio, set a limit, approve anything, or publish a report. Every
tool call is made by deterministic code through Keelgate's gateway, and the gateway, not the
caller, decides.

## What the integration provides

| Concern | How |
|---|---|
| Tool registration | `governance/_tools.py` declares nine tools with a capability, side effect, timeout and cost. A `Tool` is not callable; only the gateway runs it. |
| Capability grants | One signed, expiring grant **per agent**, exact capabilities only (Keelgate refuses wildcards). The news agent holds `news:read` and nothing else. |
| Policy | Keelgate's `finance_basic` pack plus a small fail-closed overlay (below). |
| Approvals | A paper trade always waits for a human; the approval is bound to the exact arguments, single-use, tenant-scoped. |
| Audit | Every call, refusal and decision is a record in a hash-chained, per-tenant log; `verify_audit()` fails on tampering. |
| Loop | The draft, verify, revise cycle runs on Keelgate's `Loop`: revision limit, token and time budgets, checkpoints. |
| Telemetry | OpenTelemetry spans with GenAI attributes. Keelgate's own telemetry module is not implemented yet. |
| MCP | Keelgate's `GovernedMCPServer` exposes the same tools, over stdio only. |

### Capabilities per agent

| Agent | Capabilities |
|---|---|
| `forecast-agent` | `forecast:run`, `calibration:run` |
| `risk-agent` | `risk:compute` |
| `backtest-agent` | `backtest:run` |
| `news-agent` | `news:read` |
| `fundamentals-agent` | `fundamentals:read` |
| `report-composer` | `report:write` |
| `trade-proposer` | `trade:paper_execute` (never auto-approved) |
| `api-reader` (REST) | the six read-only analytics capabilities |
| `mcp-agent` (MCP) | the read-only analytics, plus `trade:paper_execute` (proposals wait for a human) |

A grant carries a spend cap and expires; the runtime re-issues it at half its lifetime, so a
long-running server neither stops at expiry nor runs out of budget (a bug the tests found).

### The policy overlay

`finance_basic` is deny-by-default and only knows the capabilities it was written for. The
overlay (`governance/_policy.py`) adds two things and removes none:

1. the read-only analytics capabilities are allowed, and only as `READ`;
2. a paper trade that the pack would `ALLOW` is raised to `REQUIRE_APPROVAL` (at least one
   click, more for large notionals). The overlay can raise a decision or deny; it never lowers one.

Anything wrong at all is a denial. A property test checks, over generated inputs, that a trade
capability is never `ALLOW`. The pack's own limits still apply: per-action cap, daily exposure,
restricted symbols, and trading hours (so a proposal as of a Saturday is denied, not queued).

## Trust boundaries

* **`as_of` is not an argument.** The harness binds it in a `ContextVar` around each call and the
  tool bodies read it there. A tool input that includes `as_of` is rejected as invalid.
* **News is untrusted data.** Documents are read only by deterministic code; only numeric scores
  leave. Instruction-like documents are flagged, excluded from the aggregate, and disclosed in
  the report. No document text reaches a prompt (a test inspects every request).
* **Tenants are isolated**: grants, approvals, audit chains, paper books and stored reports.

## Differences between the plan and Keelgate as it is today

Written down because the brief for this phase assumed things Keelgate does not yet provide:

| Assumed | What is real | What Tycheon did |
|---|---|---|
| `keelgate>=0.1,<0.2` installable from PyPI | Not on PyPI; no git tags | The extras declare the range; `[tool.uv.sources]` pins a commit for development and CI. A published `tycheon[agents]` needs Keelgate released first. |
| Register outcome metrics via `keelgate.outcome_metrics` | The group name is stable, but `keelgate.evals` is empty and the `OutcomeMetric` protocol is not defined | Metrics are registered under the group in a shape of Tycheon's own (`tycheon.metrics`), documented as a stand-in. Keelgate discovering them cannot be tested yet. |
| Evals "using Keelgate's framework" | There is no eval framework yet | The evals are pytest suites (marker `eval`) on the real gateway with `keelgate.testing.FakeLLM`. |
| `keelgate.telemetry` | Empty module | OpenTelemetry API used directly in `governance/_telemetry.py`. |
| MCP for Claude, ChatGPT, Cursor | Keelgate's HTTP transport shares one grant across callers and its per-request hook sees no caller identity | stdio only. Not exposed over HTTP until Keelgate can authenticate callers. |
| `mcp>=1.2,<2` (Tycheon T0) | Keelgate's MCP adapter needs `mcp>=2.3,<3` | Tycheon's `serve` extra follows Keelgate. |

## Known limits

- **A native-library conflict on Linux.** Keelgate's in-process Rego engine loads `regopy`; importing it before `duckdb` corrupts the heap and aborts the process, while the reverse order is fine (found when CI crashed with exit code 134). `tycheon.governance` imports `duckdb` first and pytest opts out of Keelgate's auto-loaded plugin (`-p no:keelgate`). Any application that imports `keelgate` itself before `duckdb` on Linux can hit it; this is worth reporting upstream.
- The verifier is rule-based: it catches wrong numbers, bad citations and missing disclosures, not
  a subtly misleading but numerically correct sentence.
- The signing key is generated per process unless one is supplied; multi-process deployments must
  supply a shared key and a shared state directory.
- Jobs are in memory; a restart loses them. There is no built-in rate limiting beyond bounded jobs
  and bounded inputs: put the API behind a proxy that does.
- Keelgate's provider clients are tested through mocked transports by Keelgate; Tycheon's real-model
  path was not run against a live provider in CI.
