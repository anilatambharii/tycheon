# Tycheon Cloud (Enterprise Edition)

Proprietary. See [`LICENSE`](LICENSE) in this directory — **not** the Apache-2.0
license at the repository root.

| Path | Status | Contents |
|---|---|---|
| `control_plane/` | built (T5) | Multi-tenant API: orgs, users, API keys, OIDC SSO, Postgres RLS, rate limits, metering and quotas, BYO credentials under envelope encryption, Stripe (test mode), MCP endpoint. |
| `dashboard/` | built (T5) | Next.js + TypeScript + Tailwind customer UI (a backend-for-frontend keeps the session out of the browser). |
| `finetune/` | built (T5) | Per-tenant Kronos fine-tuning, promotion gate, private model registry and routing. |

## Why the boundary sits here

The forecasting, calibration, risk and evaluation libraries are Apache-2.0 and
stay that way: a risk number nobody can audit is worthless. What is proprietary
is the hosted operation around them — multi-tenancy, billing, the managed
dashboard and private fine-tunes.

The reasoning and the rules for moving code across this line are in
[`docs/adr/0001-licensing-and-open-core.md`](../docs/adr/0001-licensing-and-open-core.md).

Nothing in `ee/` may be imported by anything in `src/tycheon/`. The dependency
only ever points inward: Cloud depends on OSS, never the reverse.

No market data is redistributed here. Cloud customers bring their own data
license.

See [`docs/cloud.md`](../docs/cloud.md) for the architecture, the local run book and, plainly, what is
and is not verified.
