# Tycheon Cloud

Tycheon Cloud is the hosted product around the open-source library: multi-tenant API, API keys,
single sign-on, metered usage and billing, per-tenant fine-tuned models and a dashboard. Its source
is in [`ee/`](https://github.com/anilatambharii/tycheon/tree/main/ee) under a proprietary licence
(see [ADR 0001](adr/0001-licensing-and-open-core.md) and [ADR 0009](adr/0009-commercial-layer.md)).
Nothing in the open-source package imports it.

For research and risk analytics. Not investment advice.

## What a customer gets

| | Developer | Startup | Enterprise |
|---|---|---|---|
| Price | free | $1,000 / month (14-day trial) | custom |
| Forecast calls / month | 1,000 | 1,000,000 | contract |
| Data | end-of-day, research use | end-of-day | end-of-day and intraday |
| Calibration reports | no | yes | yes |
| MCP endpoint | no | yes | yes |
| Fine-tuning, dedicated GPUs, SSO, private deployment | no | no | yes |

Plans are configuration (`ee/control_plane/tycheon_cp/plans.toml`). Numbers in that file that are not
in the product brief (rate limits, retention, secondary limits, the trial length) are marked
`default`: they are placeholders to confirm, not decisions.

## How a request is handled

```mermaid
flowchart LR
  A[API key / session] --> B[tenant from the credential]
  B --> C[rate limit per org]
  C --> D[plan features + quota reservation]
  D --> E[governed tool call: grant, policy, audit]
  E --> F[tenant's data source and private models]
  F --> G[commit usage / release on failure]
```

* The organisation always comes from the credential, never from the URL or body.
* `as_of` is chosen by the server; a client that sends it (or a tenant id) gets a 422.
* Every database access runs under row-level security for that organisation.

## Fine-tuning

An Enterprise organisation uploads its own bars (CSV), submits a job, and a GPU worker trains a copy of
Kronos on them. The result is a **candidate**. It becomes servable (`model="ft:<id>"`, or `"routed"`
through the tenant's routing config) only if it passes the promotion gate on the organisation's own
walk-forward test region: clearly lower CRPS than the random walk, a significant Diebold-Mariano test,
and no worse than the base model. A model that fails is kept with the reasons and the numbers, and
cannot be served. Models are labelled with their provenance and still go through calibration.

## Running it locally

```bash
# Postgres with two roles: an owner (migrations) and an unprivileged application role
export TYCHEON_CP_MIGRATION_URL=postgresql://owner:...@localhost:5432/tycheon
export TYCHEON_CP_DATABASE_URL=postgresql://tycheon_app:...@localhost:5432/tycheon
export TYCHEON_CP_SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export TYCHEON_CP_LOCAL_KMS_KEY=$(python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())")
export STRIPE_SECRET_KEY=sk_test_...            # test mode only; a live key is refused
python -m tycheon_cp.cli migrate
python -m tycheon_cp.cli serve                  # API on 127.0.0.1:8080
python -m tycheon_ft.worker --pool shared       # a fine-tune worker
```

`ee/scripts/acceptance_t5.py` runs the whole local acceptance story (sign up, add a CSV, forecast,
risk, fine-tune, usage, Stripe invoice preview) and reports what it could and could not verify.

## What is verified, and what is not

Verified by tests that run on a real Postgres: cross-tenant isolation (data, models, credentials,
jobs, usage), metering accuracy including under concurrency, plan gating, quotas, rate limits,
webhook signature and ordering, OIDC token validation (including algorithm-confusion and replay),
envelope encryption, the promotion gate on real walk-forward scores, and the leakage guarantees of
the fine-tune datasets.

**Not verified:** anything against live Stripe (the code is exercised against a recording fake and
signed fixture events, and live keys are refused); AWS KMS (stub only); GPUs (training and serving run
on CPU with a miniature model in tests and the real Kronos-mini in the acceptance script); a browser
(the dashboard is type-checked, linted, unit-tested and built, and smoke-tested over HTTP, never
driven by a person); multi-replica behaviour.

## Known gaps

* Rate limiting is per process (no shared limiter yet).
* Blob storage is the local filesystem; an S3-compatible store is not implemented.
* Management actions are audited and role-checked but are not Keelgate tool calls.
* A fine-tune worker that dies leaves its job `running`; there is no lease or heartbeat yet.
* No commercial market-data vendor adapters: customers upload CSV; bring-your-own credentials are
  stored encrypted and ready for the providers that will use them.
* Overage pricing above a plan's quota is not defined: quotas are hard limits.
