# ADR 0009: The commercial layer (tenancy, metering, billing, fine-tuning)

Status: accepted (Phase T5)

## Context

Tycheon Cloud is the hosted operation around the open-source library: organisations, API keys,
single sign-on, metered usage, a Stripe subscription, per-tenant fine-tuned models and a dashboard.
ADR 0001 fixes the licensing boundary: the library stays Apache-2.0; the hosted operation lives in
`ee/` and the dependency points only one way.

## Decisions

**Isolation is the database's job, not each handler's.** Every tenant table has `org_id` and a
row-level-security policy `org_id = cp_current_org()`. The application connects as a role that owns
nothing and has no `BYPASSRLS`, and sets `app.org_id` per transaction from the authenticated
principal alone. With no organisation set, no row matches. The few lookups that must happen before
an organisation is known (login, API-key authentication, webhooks, the fine-tune queue, retention)
are narrow `SECURITY DEFINER` functions. A handler that forgets `WHERE org_id = ...` therefore leaks
nothing. *Limit:* any code that can run SQL as the application role could set `app.org_id` itself;
RLS defends against bugs, not against a compromised application process.

**No ORM, no psycopg.** Plain SQL over `asyncpg` (Apache-2.0). The OSS `serve` extra already lists
`psycopg` (LGPL), which is an open question for the maintainers; nothing in `ee/` uses it.

**Data-plane calls go through the same governed runtime as the agents.** A request is
authenticated, rate-limited, plan-gated and quota-reserved, then run as a Keelgate tool call (signed
capability grant, policy, audit) bound to the calling tenant's data source and private models. `ee/`
imports Keelgate only through `tycheon.governance`. Two generic hooks were added to the OSS runtime
for this (a per-call `DataSource` and a `ModelResolver`); the OSS default provides neither.
*Management-plane* actions (organisations, keys, billing, fine-tune submission) are not Keelgate tool
calls: they are authorised by role and written to the tenant's append-only audit log. That is a
deliberate scoping choice, not an oversight.

**Metering reserves before it runs.** One atomic `INSERT ... ON CONFLICT DO UPDATE ... WHERE used + q
<= limit` per call, committed on success and released on failure, with an idempotency key. The counter
always equals the sum of recorded events. Compute meters (backtest seconds, GPU seconds) check
headroom first and record exactly afterwards, so one job can overshoot its limit by its own length.

**Billing is Stripe in test mode, enforced in code.** The Stripe wrapper refuses any key that is not
`sk_test_`/`rk_test_`; there is no setting to turn that off. Prices and meters come from `plans.toml`.
Webhooks are signature-verified, de-duplicated, ordered by event time, and map to a plan only through
the *price* a subscription holds. Enterprise terms and one-off invoices for custom builds are an
operator action (a separate token), never something a customer can do to itself.

**A fine-tuned model must earn promotion.** Training windows end before the tenant's walk-forward
test region with an embargo, and that is re-checked on the finished windows. The candidate is scored
on that region against the random walk and against the base model it came from; it is promoted only
if its CRPS is clearly lower than the random walk's, a one-sided Diebold-Mariano test agrees, and it is
no worse than its base. Too few test origins fails. The database refuses `promoted` without a passing
gate in the stored evidence.

**Secrets are envelope-encrypted.** Each credential has its own AES-256-GCM data key, wrapped by a KMS
key; the additional authenticated data binds the ciphertext to `(org, credential, purpose)`, so a row
copied to another tenant does not open. A local provider exists for development and is refused in
production; the AWS KMS provider is tested against a stubbed client only.

## Consequences

* Postgres is required, and RLS can only be tested on a real Postgres: those tests are marked
  `postgres` and need `TYCHEON_TEST_DATABASE_URL` / `TYCHEON_TEST_APP_URL`; CI provides a service container.
* The rate limiter is in-process: with several replicas the effective limit is multiplied.
* Reports are not persisted by the control plane (they live in the governed runtime's store).
* Everything that needs live services (Stripe, AWS KMS, a GPU, a browser) is listed as unverified in
  `docs/cloud.md`.
