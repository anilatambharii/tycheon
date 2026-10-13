# Runbook: erase an organisation (right to erasure)

**When:** a customer asks to delete their account and data (for example a GDPR Article 17
request), or an owner uses the dashboard or API to close the account.

## What it does
`POST /v1/org/erase` with `{"confirm_slug": "<the organisation's slug>"}`, signed in as the
**owner** (an API key or any other role is refused). Irreversible. In order:

1. **Stripe:** committed usage is reported and the subscription is cancelled. If either step fails
   the request fails with `502` and **nothing is erased**, so the customer is not left billed for a
   deleted account. The Stripe *customer* record is kept: invoices and tax records have their own
   retention rules and are the customer's and Tycheon's to keep.
2. **Database:** one function (`cp_erase_org`) deletes the organisation. Every tenant table
   (users, API keys, credentials, data sources and files, usage, subscription, SSO config, fine-tune
   jobs, models, routing and the **audit log**) cascades from it. It refuses (`409`) while a fine-tune
   job is `running`: cancel it and retry. It only works inside the organisation's own tenant context.
3. **Receipt:** `erasure_receipts` keeps an opaque organisation id, a SHA-256 of the slug, the role
   that asked and the timestamps. No name, email or slug is stored.
4. **Stored files:** CSV uploads and model artifacts are removed by prefix. If this fails the API still
   returns `200` with `files_erased: false` and the receipt stays **pending**.

## Operator steps
* Verify the requester is the account owner (the API already does when they call it themselves).
* If you erase on their behalf, have the owner call the endpoint or sign in as them with their consent;
  there is deliberately no operator override.
* Check pending file erasures and finish them:

  ```bash
  python -m tycheon_cp.cli erase-pending      # prints how many receipts it completed
  ```

  Run it from a CronJob daily, or after any `files_erased: false` response. A receipt that stays
  pending (`SELECT * FROM cp_pending_erasures()`) means the blob store is failing: fix that first.
* Tell the customer the date. **Backups** are not rewritten: erased data ages out of database
  snapshots with the backup retention in [backup and restore](../backup-restore.md). If you restore
  from a backup older than an erasure, **re-run the erasure for every receipt newer than that
  backup** before the restored system takes traffic.

## Not covered
* Data the customer sent to Stripe or to their own market-data vendor.
* Logs: request logs carry no tenant identifiers or payloads by design (see SLOs), so none are
  rewritten.
* Verified only against a recording Stripe fake; `cancel_subscription` has not run against Stripe.

For research and risk analytics. Not investment advice.
