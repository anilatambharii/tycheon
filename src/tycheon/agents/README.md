# `tycheon.agents` — arrives in Phase T4

This package is an intentional placeholder. It is empty through phases T0–T3.

## What it will be

A governed research workflow — a planner, domain specialists (data, covariates,
calibration, risk) and an independent verifier — that assembles forecast and
risk analyses and shows its work.

## Why it cannot be built before T4

These agents are the part of Tycheon that takes actions, so they are exactly the
part that needs a harness. They arrive only once
[`tycheon.governance`](../governance/README.md) is in place on top of
`keelgate>=0.1,<0.2`, because:

- every tool call is capability-scoped and passes a deterministic policy gate
  before it runs — prompts are never a safety control;
- every WRITE-side-effect proposal is logged to a tamper-evident audit chain and
  may require human approval;
- all context is built with an `as_of`, so an agent cannot read the future;
- all external text (news, filings, web) is untrusted **data**. Instructions
  embedded in it are never followed;
- there is no live brokerage execution in v1. Paper and simulation only, and
  only through governed tools.

## Rules

- Nothing here imports `keelgate` directly; it goes through
  `tycheon.governance`.
- The `tycheon[agents]` extra is what installs the harness.
- Agent output is research and risk analytics. Not investment advice.
