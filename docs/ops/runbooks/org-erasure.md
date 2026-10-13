# Runbook: erase an organisation (right to erasure)

**When:** a customer closes their account or asks for their data to be deleted. Erasure is
**scheduled, not immediate**, and the customer can take their data with them first.

Tycheon is normally the *processor* of a customer's data, and the customer the *controller*. This
covers closing an organisation. A person who is only a user of a customer and asks Tycheon to delete
their data should be pointed at that customer; Tycheon deletes on the customer's instruction.

## The flow
1. **Export (optional but offered first).** The owner calls `GET /v1/org/export` (add
   `?include_models=true` for the fine-tuned model files). One ZIP: users, keys (no secrets), data
   sources and the uploaded CSVs, usage, invoices, fine-tune jobs, model metadata, routing and the
   audit log, plus a manifest. Password hashes, key secrets, stored credential secrets and Stripe ids
   are never included. One export per minute.
2. **Request.** The owner calls `POST /v1/org/erase` with `confirm_slug` and `password` (an account
   that signs in with SSO must have signed in within 15 minutes instead). It returns `202` with the
   date it will run: `TYCHEON_CP_ERASURE_GRACE_DAYS` from now (default 14, 0 to 90). Nothing is
   deleted yet. Owners and admins are notified, and the request is in the audit log.
3. **Cancel.** Any admin can `POST /v1/org/erase/cancel` until it runs. `GET /v1/org/erasure` shows
   the state and, if the job is stuck, why.
4. **Execute.** The `erase-due` CronJob (every 10 minutes) runs `python -m tycheon_cp.cli erase-due`.
   For each request whose date has passed, in this order:
   1. It stops with *blocked* if a fine-tune job is running. Stripe is not touched. Cancel the job.
   2. **Stripe:** all of the organisation's usage is reported, the subscription is cancelled with a
      final invoice (`invoice_now`), and the customer's name, email, phone and address are cleared.
      Each call is safe to repeat; a failure leaves the erasure pending and it is retried. Finalised
      invoices keep their snapshot because tax and accounting law requires it.
   3. **Database:** `cp_erase_org` deletes the organisation and every tenant row (users, keys,
      credentials, data, usage, SSO configuration, jobs, models, the audit log). The database itself
      refuses before the date, or from another tenant's context.
   4. **Receipt and tombstone:** `erasure_receipts` keeps opaque ids, a keyed hash of the slug, the
      requester's pseudonymous id and role, the request date and a count of audit rows. A tombstone
      with the same content is written to the durable log outside the database.
   5. **Files** are removed. If that fails the receipt stays *pending* and the next run finishes it.
   6. The loaded fine-tuned models of the organisation are evicted; other API replicas drop them on
      next use (within about 30 seconds of a request) or at restart.
   7. Owners and admins are told it has happened.

## Operating it
* **Alerts** (see [SLOs](../slos.md)): `TycheonErasureScheduled` (ticket, informational),
  `TycheonErasureOverdue` (page: the job is failing or blocked), `TycheonErasureFilesPending`.
  Metrics are counts only: `tycheon_erasures_scheduled`, `tycheon_erasures_overdue`,
  `tycheon_erasure_oldest_overdue_seconds`, `tycheon_erasure_files_pending`.
* **Overdue.** Read the CronJob logs and `GET /v1/org/erasure` (the `blocked_reason`). The usual
  causes are a running fine-tune job, Stripe being unreachable, or billing not being configured while
  a subscription exists.
* **Mail.** The control plane has no mail transport. Until a real `Notifier` is installed
  (`cp.extras["notifier"]`), the default only logs how many people would have been told; the same
  facts are visible to admins through the API. Do not take customers before this is wired.
* **Tombstone log.** Set `TYCHEON_CP_ERASURE_LOG_DIR` to a write-once volume (an object-lock bucket
  through a CSI driver) that a database restore does not touch. The default is inside the storage
  root, which is **not** durable enough for production.
* **No operator override.** An owner who has left the company cannot be bypassed by one person. If
  you must erase without them, two named people approve it in writing, then one inserts the request
  through a reviewed SQL change as the database owner; the normal job and grace period still apply.
  Record the approvals with the receipt.

## After restoring a backup
The restored database may contain organisations that were erased since the backup was taken.
**Before the restored system takes traffic**, replay the tombstone log:

```bash
TYCHEON_CP_MIGRATION_URL=<owner connection string> python -m tycheon_cp.cli reapply-erasures
```

It erases again every organisation in the log (and its files) and recreates any receipt the restore
lost. The function it calls is deliberately not available to the application role.

## What this does not do
* **Backups** are not rewritten. Erased data stays in database snapshots until they expire (see
  [backup and restore](../backup-restore.md) for the retention); state that period to the customer.
* Data held by **Stripe** beyond what invoices require is cleared; the invoices themselves remain.
  Stripe is a sub-processor and must be listed as such in the DPA and the processing record.
* **Logs**: request logs carry no tenant identifiers or payloads; infrastructure logs (load
  balancer, flow logs) hold network addresses for their own retention period.
* **Stale sessions** of an erased organisation fail with `401` on any write; reads of an erased
  account return nothing. They expire within the session lifetime (8 hours).
* **Live Stripe**: the Stripe calls (`retrieve`, `cancel` with `invoice_now`, customer update) are
  tested against a mocked SDK only. Verify with a test key before relying on them.

For research and risk analytics. Not investment advice.
