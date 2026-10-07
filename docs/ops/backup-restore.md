# Backup and restore

What is backed up, how often, how to restore, and how that was tested.

## What holds state

| State | Where | Backed up by | Notes |
|---|---|---|---|
| Control-plane database (orgs, users, keys, usage, credentials, models registry, audit log) | PostgreSQL (RDS / Cloud SQL) | automated snapshots + point-in-time recovery (Terraform: `backup_retention_days`, default at least 7) and a **manual snapshot before every production deploy** (`images.yml`) | the source of truth |
| Uploaded customer data and model artifacts | the blob store (a ReadWriteMany volume today; S3 is future work) | volume snapshots / bucket versioning, per the infrastructure module | the database holds hashes: a restore checks artifacts against them |
| Envelope-encryption keys | KMS | KMS key rotation and deletion protection (Terraform); never exported | losing the KMS key makes stored credentials unrecoverable *by design* |
| Open-source API audit and approvals | `/data/state` (SQLite) | volume snapshot if `api.persistence.enabled` | the audit chain is hash-linked: a restored copy verifies |
| Secrets (Stripe test keys, session secret, tokens) | Kubernetes Secrets, synced from a secrets manager | the secrets manager | rotate rather than restore (see [credentials leak](runbooks/credentials-leak.md)) |

Targets (to be confirmed with real infrastructure): recovery point **5 minutes** with point-in-time
recovery, recovery time **under 4 hours** for a full database restore.

## Restore, step by step

1. **Stop writes.** Scale the control plane and workers to zero
   (`kubectl -n tycheon scale deploy --all --replicas=0`), or put the ingress in maintenance.
2. **Restore the database** to a *new* instance from a snapshot or a point in time (RDS: "restore to
   point in time"; never overwrite the damaged instance until the restored one is verified).
3. **Verify the restored database** (`deploy/ops/backup_restore_test.sh` shows the checks):
   the migration history matches what the release expects, row counts are plausible, the
   application role is still not a superuser and still has no `BYPASSRLS`, and tenant isolation
   still holds (a tenant sees only its own rows; with no tenant set, no rows).
4. **Point the release at it** (new endpoint in the database Secret) and, if the restored schema is
   older than the release, run `python -m tycheon_cp.cli migrate` (pending migrations apply) or
   roll the release back first (see [bad migration](runbooks/bad-migration.md)).
5. **Scale back up**, run `helm test`, and watch the SLO dashboards for a full hour.
6. **Tell customers** what the recovery point was, if any data after it was lost.

## How this is tested

`deploy/ops/backup_restore_test.sh` is a real restore drill, run against a throwaway database:
it migrates, creates organisations, keys, credentials, usage and audit entries, takes a
`pg_dump` (custom format), **drops the database**, restores it with `pg_restore`, and then checks that:

* every table's row count is identical to before the dump;
* the migration history is identical;
* the application role is still unprivileged and tenant isolation still holds (tenant A cannot see
  tenant B's rows, and no tenant means no rows);
* the append-only audit log is still append-only for the application role;
* an encrypted credential still decrypts after the restore (the envelope metadata survived).

The script is run in CI-like conditions and its output is recorded in the Phase T6 pull request. It
proves that the *logical* backup and restore procedure preserves data and its security properties.
It does **not** prove anything about RDS snapshots, point-in-time recovery, or cross-region restore:
those need a real account and a scheduled drill (see the launch checklist).

## Drill schedule

* Run the script on every migration change (it is cheap).
* Run a full restore drill into a staging account **quarterly**, time it against the recovery-time
  target, and record the result in the incident log.
