"""A real restore drill for the control-plane database, against a throwaway database.

    TYCHEON_TEST_DATABASE_URL=<owner role url> \\
    TYCHEON_TEST_APP_URL=<application role url> \\
    python deploy/ops/backup_restore_test.py [--pg-prefix "wsl.exe -d Ubuntu -u root --"]

It builds a database with real data (through the same code paths the API uses), takes a logical
backup (`pg_dump -Fc`), DROPS the database, restores it (`pg_restore`), and checks that nothing was
lost and, as importantly, that the security properties survived: the application role is still
unprivileged, tenant isolation (row-level security) still holds, the audit log is still append-only
for the application, and an encrypted credential still decrypts.

It proves the logical procedure. It does not test RDS snapshots, point-in-time recovery or
cross-region restore: those need a real account (see docs/ops/backup-restore.md).

Proprietary: see ee/LICENSE.
"""

# DuckDB first: it cannot share a process with Keelgate's regopy once both are in use.
import duckdb  # noqa: F401
import argparse
import asyncio
import base64
import os
import shlex
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import asyncpg
from tycheon_cp import store
from tycheon_cp.crypto import LocalKms
from tycheon_cp.db import Database, migrate

APP_ROLE = "tycheon_app"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}", flush=True)


def with_db(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def pg_tool(prefix: list[str], tool: str, *args: str, stdin: bytes | None = None) -> bytes:
    done = subprocess.run([*prefix, tool, *args], input=stdin, capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{tool} failed: {done.stderr.decode()[:500]}")
    return done.stdout


async def snapshot(dsn: str) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    try:
        tables = [r["t"] for r in await conn.fetch(
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1")]
        counts = {t: await conn.fetchval(f'SELECT count(*) FROM "{t}"') for t in tables}  # noqa: S608
        migrations = [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations ORDER BY 1")]
        policies = [tuple(r) for r in await conn.fetch("SELECT tablename, policyname FROM pg_policies ORDER BY 1, 2")]
        functions = [r["proname"] for r in await conn.fetch(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' ORDER BY 1")]
        rls = [tuple(r) for r in await conn.fetch(
            "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind = 'r' ORDER BY 1")]
        return {"counts": counts, "migrations": migrations, "policies": policies,
                "functions": functions, "rls": rls}
    finally:
        await conn.close()


async def main(args: argparse.Namespace) -> int:
    owner_url = os.environ.get("TYCHEON_TEST_DATABASE_URL")
    app_url = os.environ.get("TYCHEON_TEST_APP_URL")
    if not (owner_url and app_url):
        print("set TYCHEON_TEST_DATABASE_URL and TYCHEON_TEST_APP_URL")
        return 2
    prefix = shlex.split(args.pg_prefix) if args.pg_prefix else []
    name = f"tycheon_restore_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(owner_url)
    await admin.execute(f'CREATE DATABASE "{name}"')
    owner, app = with_db(owner_url, name), with_db(app_url, name)
    print(f"== drill database {name}")
    try:
        print("== 1. build a database with real data")
        await migrate(owner, app_role=APP_ROLE)
        db = await Database.connect(app)
        kms = LocalKms(base64.b64encode(os.urandom(32)).decode())
        orgs = []
        for i in range(3):
            async with db.anonymous() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM cp_signup($1, $2, $3, $4)", f"drill-{i}-{uuid.uuid4().hex[:6]}",
                    f"Drill {i}", f"drill{i}-{uuid.uuid4().hex[:6]}@example.com", "x")
            principal = store.Principal(row["org_id"], "session", frozenset({"admin"}), "owner", row["user_id"])
            await store.create_api_key(db, principal, name=f"key-{i}", scopes=["analytics"])
            cid = await store.create_credential(db, kms, principal, name="vendor", provider="drill", secret=f"secret-{i}")
            async with db.tenant(row["org_id"]) as conn:
                for n in range(5 + i):
                    await conn.execute(
                        "INSERT INTO usage_events (org_id, kind, quantity, idempotency_key) "
                        "VALUES ($1, 'forecast_calls', 1, $2)", row["org_id"], f"drill-{i}-{n}")
            orgs.append((row["org_id"], cid, f"secret-{i}"))
        await db.close()
        before = await snapshot(owner)
        print(f"   tables: {len(before['counts'])}, rows: {sum(before['counts'].values())}")

        print("== 2. back up, drop the database, restore")
        dump = pg_tool(prefix, "pg_dump", "--format=custom", "--no-owner", "--dbname", owner)
        check("backup produced", len(dump) > 1000, f"{len(dump)} bytes")
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        gone = await admin.fetchval("SELECT count(*) FROM pg_database WHERE datname = $1", name)
        check("the database is really gone before the restore", gone == 0)
        await admin.execute(f'CREATE DATABASE "{name}"')
        pg_tool(prefix, "pg_restore", "--no-owner", "--role", "tycheon_owner", "--dbname", owner, stdin=dump)
        after = await snapshot(owner)

        print("== 3. nothing lost")
        check("every table has the same row count", after["counts"] == before["counts"])
        check("the migration history is identical", after["migrations"] == before["migrations"],
              f"{len(after['migrations'])} applied")
        check("row-level security is enabled on the same tables", after["rls"] == before["rls"])
        check("the same policies exist", after["policies"] == before["policies"])
        check("the same functions exist", after["functions"] == before["functions"])

        print("== 4. the security properties survived the restore")
        probe = await asyncpg.connect(owner)
        flags = await probe.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", APP_ROLE)
        await probe.close()
        check("the application role is neither superuser nor BYPASSRLS",
              not flags["rolsuper"] and not flags["rolbypassrls"])
        db = await Database.connect(app)
        (a, a_cred, a_secret), (b, _, _) = orgs[0], orgs[1]
        async with db.tenant(a) as conn:
            mine = await conn.fetchval("SELECT count(*) FROM usage_events")
            theirs = await conn.fetchval("SELECT count(*) FROM usage_events WHERE org_id = $1", b)
        check("a tenant sees only its own rows after the restore", mine == 5 and theirs == 0, f"own={mine}, other={theirs}")
        async with db.anonymous() as conn:
            check("with no tenant set, no tenant row is visible",
                  await conn.fetchval("SELECT count(*) FROM api_keys") == 0)
            try:
                await conn.execute("UPDATE audit_log SET action = 'forged'")
                audit_ok = False
            except asyncpg.InsufficientPrivilegeError:
                audit_ok = True
        check("the audit log is still append-only for the application", audit_ok)
        opened = await store.open_credential(db, kms, a, a_cred)
        check("an encrypted credential still decrypts", opened == a_secret)
        try:
            await store.open_credential(db, kms, b, a_cred)
            cross = False
        except Exception:
            cross = True
        check("and still will not open for another tenant", cross)
        await db.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{'RESTORE DRILL PASSED' if not failed else 'RESTORE DRILL FAILED'}: "
          f"{len(results) - len(failed)}/{len(results)} checks")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-prefix", default="", help='prefix for pg_dump/pg_restore, e.g. "wsl.exe -d Ubuntu -u root --"')
    sys.exit(asyncio.run(main(parser.parse_args())))
