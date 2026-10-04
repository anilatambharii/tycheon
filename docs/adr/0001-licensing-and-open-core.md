# 0001. Licensing and open core

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** T0

## Context

Tycheon ships as two products from one repository: Tycheon OSS and Tycheon
Cloud. We need a licence split that is defensible to three audiences at once.

A quant or risk engineer will not trust a number they cannot audit. The entire
value of a calibration claim is that someone can re-run the evaluation and check
it. A closed forecasting core would be worth very little, because the thing we
are selling is honesty about uncertainty, and honesty has to be inspectable.

A fintech integrating Tycheon needs to know its own product is not infected by
our licence choice, and that a dependency cannot be pulled out from under it.

And the project needs a commercial engine, or it will not be maintained long
enough to be trustworthy.

We also inherit obligations. Kronos is MIT, and vendoring it means retaining
its notice. Market data is licensed per customer, which means no deployment of
ours may redistribute exchange data regardless of code licence.

## Decision

1. **Everything outside `ee/` is Apache-2.0.** That includes `src/tycheon/`
   (data, models, calibration, covariates, routing, risk, backtest, governance,
   agents, serve), `benchmarks/`, `tests/`, `examples/`, `docs/` and `deploy/`.
   Apache-2.0 over MIT for the explicit patent grant, which matters to the
   financial institutions we expect to be the users.

2. **`ee/` is proprietary**, under `ee/LICENSE`, and contains only:
   `control_plane/` (tenancy, metered billing, API keys, quotas),
   `dashboard/` (the hosted UI) and `finetune/` (proprietary fine-tuning and
   the private model registry).

3. **The dependency arrow points inward only.** Nothing in `src/tycheon/` may
   import from `ee/`. Cloud depends on OSS; OSS never depends on Cloud. The
   consequence is that removing `ee/` entirely leaves a complete, working
   product.

4. **Calibration, risk and evaluation never move to `ee/`.** Any method that
   affects a published number stays open, including the leaderboard harness and
   the conformal machinery. If we cannot monetise without closing the
   evaluation path, the business model is wrong, not the licence.

5. **Third-party code keeps its own notice.** Vendored upstream code lives in
   `third_party/<name>/` with its original licence file retained; Kronos is MIT
   and its notice travels with it. Heavy models stay optional extras so a base
   install carries no surprise licences.

6. **No copyleft dependencies** in the OSS core without explicit approval, so
   that a proprietary integration downstream stays possible.

7. **No market data is ever committed or redistributed**, under either licence.
   Providers are pluggable and customers bring their own data licence.
   `yfinance` is development and examples only, and never in Cloud.

## Consequences

**Good.** Every published forecast and risk number can be independently
reproduced from open code. Integrators get a patent grant and a clean licence
story. Contributors know exactly which directory accepts contributions, and we
can accept outside contributions to the core without a CLA dance over the
proprietary parts.

**Costly.** A competitor can host the open core. We accept that: the moat is
the leaderboard, the calibration track record and the operational product, not
source secrecy. We also carry the ongoing discipline of keeping `ee/` from
quietly absorbing core functionality, which is a review burden on every PR that
touches the boundary.

**Open.** `ee/LICENSE` is a placeholder pending counsel review. Its scope is
authoritative; its wording is provisional. A trademark policy for the Tycheon
name is not yet written.

## Alternatives considered

- **All Apache-2.0, no `ee/`.** Cleanest story, no sustainable funding path for
  the hosted product.
- **AGPL core with a commercial exception.** Stronger protection against
  hosting competitors, but it would stop exactly the fintech integrations we are
  built for, since many legal teams refuse AGPL outright.
- **BSL or a delayed-open licence.** Not open source at the moment of use,
  which undercuts the auditability argument that the whole project rests on.
- **Closed core with an open client.** Fails the trust test completely: a
  calibration claim nobody can check is marketing.

## Enforcement

- `tests/test_packaging.py` asserts the project licence is Apache-2.0 and the
  root `LICENSE` is the Apache text.
- `tests/test_repo_layout.py` asserts `ee/LICENSE` exists and is explicitly not
  Apache-2.0.
- `tests/test_architecture.py` holds the import boundaries, including the rule
  that Keelgate may only be imported from `src/tycheon/governance/`.
- GitHub dependency review runs on every PR and flags incompatible licences.
