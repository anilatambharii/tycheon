# `tycheon.governance` — arrives in Phase T4

This package is an intentional placeholder. It is empty through phases T0–T3.

## Why it exists now

Tycheon's sister project [Keelgate](https://github.com/anilatambharii/keelgate)
is the safety harness that governs anything with a side effect: capability-scoped
tools, deterministic policy gates, human approvals, tamper-evident audit logs and
the agent eval framework. Tycheon will depend on it as a library.

`AGENTS.md` makes this package the **single** place Keelgate may be imported.
Declaring it in T0 means the boundary exists before there is any code to tempt
us across it, and `tests/test_architecture.py` enforces it from day one.

## Rules this package lives under

- Phases **T0–T3 must not depend on Keelgate at all.** Forecasting, calibration,
  risk and evaluation are built as a pure library with no harness in the loop.
- From **T4**, Tycheon depends on `keelgate>=0.1,<0.2`, pulled in by the
  `tycheon[agents]` extra.
- **Nothing outside this package imports `keelgate`.** Everything else in
  `src/tycheon/` talks to the thin wrappers defined here.
- Only Keelgate's documented integration contract
  (its `docs/integration-contract.md`) may be used. Never `keelgate._internal`.
- Tycheon registers its financial metrics into Keelgate's eval suite through the
  `keelgate.outcome_metrics` entry-point group.
- **There is never a fallback that bypasses governance.** No "if keelgate is
  missing, execute anyway" path will be accepted in review. If the harness is
  unavailable, the governed operation fails closed.

## What will land here in T4

Thin, typed adapters over the contract — tool registration with side-effect
tags, capability grants, policy decisions, approval requests, audit writes,
`as_of` context construction and outcome-metric registration — so the rest of
Tycheon depends on Tycheon types, not on Keelgate's.
