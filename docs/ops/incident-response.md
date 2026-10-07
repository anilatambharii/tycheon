# Incident response

How Tycheon Cloud handles an incident: who does what, how severity is judged, how customers are
told, and how we learn. It is short on purpose: a plan nobody reads fails when it is needed.

## Roles
* **Incident commander (IC):** owns the incident until it is resolved. Decides, delegates, and
  keeps the timeline. The on-call engineer is IC until they hand over.
* **Operations lead:** does the technical work (diagnose, mitigate, roll back).
* **Communications lead:** writes the customer and status updates. Never the IC.
* **Security lead** (for security incidents): preserves evidence and decides on notification.

A solo operator holds all roles and writes the timeline as they go; the discipline is the same.

## Severity

| Level | Meaning | Examples | Response |
|---|---|---|---|
| **SEV1** | customers cannot use the service, or data is exposed or lost | API down; cross-tenant data visible; credentials leaked | page now; IC within 15 min; update every 30 min |
| **SEV2** | a major feature is degraded, or the error budget is burning fast | fine-tuning down; MCP failing; forecasts badly slow | page; IC within 30 min; update every hour |
| **SEV3** | a minor feature or a few customers affected, workaround exists | one customer's CSV upload failing | ticket; next working day |
| **SEV4** | no customer impact | a failed alert, a flaky test | backlog |

When in doubt, declare the higher severity; it is cheap to lower.

## The first hour
1. **Declare it.** Open an incident record (title, severity, IC, start time). Start a timeline: every
   action with a timestamp.
2. **Mitigate before diagnosing**: roll back the last change, scale out, disable the failing feature
   (a plan feature can be turned off in `plans.toml`), or block the abusive key.
   The [runbooks](runbooks/error-budget-burn.md) list the first moves for each alert.
3. **Communicate.** Post a status update within 30 minutes of a SEV1/SEV2: what is affected, what we
   know, when the next update is. Do not speculate about cause or guess a time to fix.
4. **Preserve evidence** for anything security-related *before* cleaning up: logs, the audit log
   (it is hash-chained and append-only for the application), snapshots of affected volumes.
5. **Resolve and verify**: the SLO dashboards are back to normal for a full hour.

## Security incidents
A suspected breach, cross-tenant exposure, leaked credential, or malicious use is a SEV1 until
shown otherwise.
* Contain first (revoke keys, rotate secrets: see [credentials leak](runbooks/credentials-leak.md); block the
  source; isolate the workload with a network policy).
* The audit log tells you what a principal did; row-level security tells you the database could not
  have shown one tenant another's rows, but verify rather than assume.
* **Notification.** If customer data may have been exposed, notify affected customers without undue
  delay, and regulators where the law and contracts require it (for example, GDPR within 72 hours of
  awareness, and the notification terms in each customer agreement). Counsel decides the wording.
  Report vulnerabilities in the open-source code through the process in `SECURITY.md`.

## Afterwards (within five working days)
A **blameless review**: what happened (timeline), why it was possible, what was slow, what worked,
and concrete follow-ups with owners and dates (a test, an alert, a runbook change). Publish a
customer-facing summary for any SEV1/SEV2. Follow-ups are tracked until done: an incident with
unfixed follow-ups is not closed.

## Contacts and tooling to fill in before launch
On-call rota and paging service, the status page, the security mailbox from `SECURITY.md`, legal
counsel, and cloud-provider support plans. **These are not set up yet**: see the launch checklist.
