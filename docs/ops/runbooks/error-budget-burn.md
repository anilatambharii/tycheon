# Runbook: error budget burn (`TycheonErrorBudgetFastBurn`, `TycheonErrorBudgetSlowBurn`)

**Meaning:** more than the allowed share of requests are failing with 5xx. Fast burn (page) means
the whole 30-day budget would be gone in about two days; slow burn (ticket) in about five.

## First five minutes
1. **Is it real and how wide?** Dashboard: `tycheon:http_error_ratio:rate5m`, then by route:
   `sum by (route) (rate(tycheon_http_requests_total{status_class="5xx"}[5m]))`.
   One route failing points at a dependency of that route; every route failing points at the platform.
2. **Did something change?** `helm history tycheon -n tycheon` and the deploy log. A deploy in the
   last hour is the most likely cause: **roll back first, diagnose afterwards**
   (`helm rollback tycheon -n tycheon --wait`). A rollback does not undo database migrations: if the
   release ran one, read [bad migration](bad-migration.md) before rolling back.
3. **Are the pods healthy?** `kubectl -n tycheon get pods`, `kubectl -n tycheon describe pod <p>`,
   `kubectl -n tycheon logs <p> --previous`. CrashLoopBackOff, OOMKilled (raise the memory limit),
   ImagePullBackOff (registry or digest problem).
4. **Is the database reachable?** Control-plane errors with `internal_error` and a database exception
   in the logs: check RDS / Cloud SQL status and connections (`max_connections`), then failover if the
   primary is unhealthy (a Multi-AZ failover takes one to two minutes).
5. **Is a dependency down?** The analytics need only the database and local models. Stripe or an
   identity provider being down breaks billing and SSO routes only; it must not fail forecasts. If it
   does, that is a bug: file it and add a test.

## Mitigations
* Roll back the release (above).
* Scale out if the cause is load: the HPAs do this; raise `maxReplicas` if they are pinned.
* Shed load: lower the per-plan rate limits in `plans.toml` and redeploy, as a last resort.

## Afterwards
Write the incident record ([incident response](../incident-response.md)), add the missing test or
alert, and note how much budget was spent.
