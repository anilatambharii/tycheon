# Runbook: quota refusals (`TycheonQuotaRefusalsSpike`)

**Meaning:** many requests are being refused with `quota_exceeded` (HTTP 429). Quotas are hard
limits by design; refusals are normal for a customer who has used their plan, and a problem only if
the quota is wrong or a limit is mis-set.

1. **One tenant or many?** Many tenants at once suggests a bad plan limit after a deploy
   (`plans.toml`) or a clock problem (counters are per UTC calendar month; a wrong clock on a pod
   would put it in the wrong period). Check pod time and the period in `GET /v1/usage`.
2. **A real customer over their plan:** they need an upgrade, or (Enterprise) an operator override:
   `PUT /operator/orgs/<slug>/subscription` with `limits_override`. Record who approved it.
3. **Counters look too high:** the reservation counter always equals the sum of recorded events
   (tested). If a customer disputes their usage, compare `usage_counters` with
   `SELECT sum(quantity) FROM usage_events` for the period; they should match exactly.
4. **Never** edit `usage_counters` by hand to "give a customer more": raise the limit instead, so
   counters and events stay reconcilable.
