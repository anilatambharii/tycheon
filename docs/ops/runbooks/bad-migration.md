# Runbook: a bad migration or a failed release

Migrations are plain SQL files, each with a paired `.down.sql`, applied by a Helm pre-upgrade Job as
the database owner role. A failed migration fails the release (`--atomic` rolls the Deployments back)
and leaves the database as it was (each migration runs in one transaction).

## The migration Job failed
Nothing changed. `kubectl -n tycheon logs job/<release>-migrate-<n>`, fix the migration, release again.

## The migration succeeded but the release is bad
`helm rollback tycheon -n tycheon --wait` puts the old pods back; **it does not undo the schema**.
Decide:

1. **Is the old code compatible with the new schema?** Migrations are written to be additive
   (new columns, new functions) so the previous release keeps working. If so, leave the schema and
   fix forward. This is the normal case.
2. **Is it not?** Roll the schema back, one migration at a time, newest first:
   ```
   kubectl -n tycheon run migrate-down --rm -it --restart=Never --image=<cloud image> \
     --env TYCHEON_CP_MIGRATION_URL=... -- python -m tycheon_cp.cli rollback --steps 1
   ```
   The command refuses a migration whose down script drops a table unless you add `--allow-data-loss`.
   Every down script is tested to reproduce the earlier schema exactly (`ee/tests/test_migrations.py`).
3. **Data was lost or corrupted?** Do not roll back: **restore** to a new instance from the snapshot
   the pipeline took before the deploy, following [backup and restore](../backup-restore.md).

## After
Add a test that would have caught it, and note in the migration whether the previous release is
compatible with it.
