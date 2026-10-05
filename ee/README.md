# Tycheon Cloud (Enterprise Edition)

Proprietary. See [`LICENSE`](LICENSE) in this directory — **not** the Apache-2.0
license at the repository root.

| Path | Status | Contents |
|---|---|---|
| `control_plane/` | planned | Tenancy, metered billing, API keys, usage limits. |
| `dashboard/` | planned | Next.js + TypeScript + Tailwind operator and customer UI. |
| `finetune/` | planned | Proprietary fine-tuning and private model registry. |

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
