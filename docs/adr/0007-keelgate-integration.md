# 0007. Integrating Keelgate

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** T4

## Context

AGENTS.md makes Keelgate the only thing that may govern a side effect, and confines every import
of it to `src/tycheon/governance/`. Phase T4 is where that boundary acquires code. Reading
Keelgate's integration contract and source showed it is further from the brief than assumed: its
`telemetry` and `evals` modules are empty, its `OutcomeMetric` protocol is not defined, it is not
on PyPI and has no tags, and its MCP HTTP transport shares one grant across callers.

## Decision

1. **One module, no bypass.** All `keelgate` imports are in `tycheon.governance`. Tests enforce:
   no import elsewhere, no use of Keelgate internals, no `try/except ImportError` fallback around a
   Keelgate import, and no call to the raw analytics functions from `serve/` or `agents/` (so the
   governed tool is the only way in).
2. **A policy overlay, not a new pack.** Keelgate's `finance_basic` is vetted and deny-by-default
   but does not know Tycheon's analytics capabilities and auto-allows small paper trades. A small
   Python `PolicyEngine` wraps it: analytics are allowed as `READ` only; a paper trade the pack
   would allow is raised to `REQUIRE_APPROVAL`; it can raise or deny, never lower; any error is a
   denial. This avoids writing new Rego (a place Keelgate's own history found a fail-open) while
   making "never auto-approved" a tested property.
3. **Least-privilege grants per agent**, exact capabilities, re-issued before expiry with a fresh
   budget window. REST and MCP callers get their own service principals.
4. **`as_of` is trusted context, not an argument.** Services read `as_of`, tenant and data from a
   `ContextVar` the runtime binds around each gateway call. Tool inputs have no `as_of` field and
   unknown fields are rejected.
5. **The draft/verify/revise cycle runs on Keelgate's `Loop`**, with Tycheon's writer as its
   planner role and Tycheon's verifier as its verifier role, to get budgets, revision limits,
   checkpoints and audit rather than reimplement them.
6. **MCP over stdio only.** Keelgate's HTTP transport cannot identify callers.
7. **Dependencies.** `keelgate>=0.1,<0.2` in the `agents` and `serve` extras (never the base
   install). Until Keelgate is released, `[tool.uv.sources]` pins a commit (uv-only, not in
   published metadata). `mcp` moves to `>=2.3,<3` to match Keelgate.
8. **Gaps are stand-ins, labelled.** Telemetry uses the OpenTelemetry API directly; outcome
   metrics are registered under Keelgate's entry-point group in a shape of Tycheon's own
   (`tycheon.metrics`); evals are pytest suites on `keelgate.testing.FakeLLM`. Each is a small,
   isolated change when Keelgate ships the real thing.

## Consequences

- A published `tycheon[agents]` is not installable until Keelgate is on PyPI.
- Whether Keelgate can discover Tycheon's metrics is untested until it defines the protocol.
- Tycheon is coupled to one Keelgate commit until then; the architecture tests keep the blast
  radius to one package.

## Alternatives considered

Importing Keelgate across the codebase (rejected: AGENTS.md); a new Rego pack (more safety-critical
code to get right); trusting the model-supplied `as_of` (rejected: a model could ask for the
future); exposing MCP over HTTP with one shared grant (a confused deputy).
