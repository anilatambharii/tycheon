"""Repository functions: every database access of the control plane, each inside one tenant.

Application code never writes SQL outside this module and the other repositories, and every
function that touches a tenant table takes its connection from ``Database.tenant``, so row-level
security applies whether or not a query remembers its ``WHERE org_id``.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import asyncpg

from tycheon_cp.crypto import SealedSecret, open_sealed, seal
from tycheon_cp.errors import ApiProblem, not_found
from tycheon_cp.security import (
    SCOPES,
    AuthError,
    NewApiKey,
    Role,
    Session,
    hash_password,
    key_matches,
    new_api_key,
    normalize_email,
    parse_api_key,
    verify_password,
)

if TYPE_CHECKING:
    from tycheon_cp.crypto import Kms
    from tycheon_cp.db import Database
    from tycheon_cp.plans import Plan, Plans

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")
#: A subscription in one of these states keeps its plan; anything else falls back to Developer.
ENTITLED = frozenset({"active", "trialing", "past_due"})


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:38].strip("-")
    return slug if _SLUG.fullmatch(slug) else ""


@dataclass(frozen=True)
class Principal:
    """Who is calling: an organisation, how they proved it, and what they may do."""

    org_id: UUID
    kind: str  # "api_key" | "session"
    scopes: frozenset[str]
    role: Role | None = None
    user_id: UUID | None = None
    key_id: UUID | None = None

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}" if self.user_id else f"key:{self.key_id}"


@dataclass(frozen=True)
class OrgContext:
    org_id: UUID
    slug: str
    name: str
    plan: Plan
    status: str
    override: dict[str, Any]


# ---------------------------------------------------------------------------- accounts
async def signup(
    db: Database, *, org_name: str, slug: str | None, email: str, password: str
) -> tuple[UUID, UUID]:
    email = normalize_email(email)
    name = org_name.strip()
    if not 1 <= len(name) <= 120:
        raise ApiProblem(
            422, "invalid_request", "The organisation name must be 1 to 120 characters."
        )
    chosen = (slug or slugify(name)).strip().lower()
    if not _SLUG.fullmatch(chosen):
        raise ApiProblem(422, "invalid_request", "The organisation slug is not valid.")
    password_hash = hash_password(password)
    try:
        async with db.anonymous() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cp_signup($1, $2, $3, $4)", chosen, name, email, password_hash
            )
    except asyncpg.UniqueViolationError as exc:
        raise ApiProblem(
            409, "conflict", "That organisation or email is already registered."
        ) from exc
    if row is None:  # pragma: no cover - the function always returns a row
        raise ApiProblem(500, "internal_error", "Sign-up failed.")
    return row["org_id"], row["user_id"]


async def login(db: Database, *, email: str, password: str) -> Session:
    """Verify a password. The same error and the same work whether or not the account exists."""
    try:
        address = normalize_email(email)
    except AuthError:
        verify_password(password, None)
        raise
    async with db.anonymous() as conn:
        row = await conn.fetchrow("SELECT * FROM cp_login_lookup($1)", address)
    ok = verify_password(password, row["password_hash"] if row else None)
    if row is None or not ok:
        raise AuthError("the email or password is not correct")
    return Session(str(row["user_id"]), str(row["org_id"]), row["role"])


# ----------------------------------------------------------------------------- API keys
async def authenticate_key(db: Database, token: str | None) -> Principal | None:
    """The principal for an API key, or ``None``. Uniform work for unknown and revoked keys."""
    parsed = parse_api_key(token)
    if parsed is None:
        return None
    prefix, secret = parsed
    async with db.anonymous() as conn:
        row = await conn.fetchrow("SELECT * FROM cp_api_key_lookup($1)", prefix)
    matched = key_matches(secret, row["secret_hash"] if row else None)
    if row is None or not matched or row["revoked"]:
        return None
    async with db.tenant(row["org_id"]) as conn:
        await conn.execute(
            "UPDATE api_keys SET last_used_at = now() WHERE id = $1 "
            "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
            row["key_id"],
        )
    return Principal(
        org_id=row["org_id"],
        kind="api_key",
        scopes=frozenset(row["scopes"]),
        key_id=row["key_id"],
    )


async def create_api_key(
    db: Database, principal: Principal, *, name: str, scopes: list[str]
) -> tuple[UUID, NewApiKey]:
    clean = sorted(set(scopes))
    if not clean or any(s not in SCOPES or s == "admin" for s in clean):
        raise ApiProblem(422, "invalid_request", "Scopes must be from: analytics, mcp.")
    key = new_api_key()
    async with db.tenant(principal.org_id) as conn:
        key_id = await conn.fetchval(
            "INSERT INTO api_keys (org_id, name, prefix, secret_hash, scopes, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            principal.org_id,
            name.strip()[:80] or "key",
            key.prefix,
            key.secret_hash,
            clean,
            principal.user_id,
        )
        await audit(conn, principal, "api_key.create", str(key_id), {"scopes": clean})
    return key_id, key


async def list_api_keys(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    async with db.tenant(org_id) as conn:
        rows = await conn.fetch(
            "SELECT id, name, prefix, scopes, created_at, last_used_at, revoked_at "
            "FROM api_keys ORDER BY created_at DESC"
        )
    return [{**dict(r), "id": str(r["id"]), "scopes": list(r["scopes"])} for r in rows]


async def revoke_api_key(db: Database, principal: Principal, key_id: UUID) -> None:
    async with db.tenant(principal.org_id) as conn:
        done = await conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL", key_id
        )
        if done != "UPDATE 1":
            raise not_found("API key")
        await audit(conn, principal, "api_key.revoke", str(key_id), {})


# ------------------------------------------------------------------------ org context
async def load_org(db: Database, org_id: UUID, plans: Plans) -> OrgContext:
    async with db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            "SELECT o.slug, o.name, s.plan, s.status, s.limits_override "
            "FROM orgs o JOIN subscriptions s ON s.org_id = o.id"
        )
    if row is None:
        raise not_found("organisation")
    entitled = row["status"] in ENTITLED
    plan = plans.get(row["plan"]) if entitled else plans.default
    override = json.loads(row["limits_override"]) if entitled else {}
    return OrgContext(org_id, row["slug"], row["name"], plan, row["status"], override)


async def me(db: Database, session: Session) -> dict[str, Any]:
    async with db.tenant(UUID(session.org_id)) as conn:
        row = await conn.fetchrow(
            "SELECT email, role FROM users WHERE id = $1 AND disabled_at IS NULL",
            UUID(session.user_id),
        )
    if row is None:
        raise AuthError("your account is no longer active")
    return {"user_id": session.user_id, "email": row["email"], "role": row["role"]}


# ------------------------------------------------------------------------------ audit
async def audit(
    conn: Any, principal: Principal, action: str, target: str | None, details: dict[str, Any]
) -> None:
    """Append to the tenant's audit log (inside the caller's transaction)."""
    await conn.execute(
        "INSERT INTO audit_log (org_id, actor, action, target, details) "
        "VALUES ($1, $2, $3, $4, $5::jsonb)",
        principal.org_id,
        principal.actor,
        action,
        target,
        json.dumps(details),
    )


async def list_audit(db: Database, org_id: UUID, limit: int = 100) -> list[dict[str, Any]]:
    async with db.tenant(org_id) as conn:
        rows = await conn.fetch(
            "SELECT id, actor, action, target, details, occurred_at FROM audit_log "
            "ORDER BY id DESC LIMIT $1",
            min(max(limit, 1), 500),
        )
    return [{**dict(r), "details": json.loads(r["details"])} for r in rows]


# ------------------------------------------------------------------------- credentials
async def create_credential(
    db: Database,
    kms: Kms,
    principal: Principal,
    *,
    name: str,
    provider: str,
    secret: str,
) -> UUID:
    """Store a bring-your-own market-data credential, sealed under the KMS. Never returned."""
    if not 1 <= len(secret) <= 8192:
        raise ApiProblem(422, "invalid_request", "The credential must be 1 to 8192 characters.")
    credential_id = uuid4()
    sealed = await seal(
        kms, secret.encode(), org_id=str(principal.org_id), credential_id=str(credential_id)
    )
    try:
        async with db.tenant(principal.org_id) as conn:
            await conn.execute(
                "INSERT INTO credentials (id, org_id, name, provider, kms_key_id, wrapped_dek, "
                "nonce, ciphertext) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                credential_id,
                principal.org_id,
                name.strip()[:80],
                provider.strip().lower()[:40],
                sealed.kms_key_id,
                sealed.wrapped_dek,
                sealed.nonce,
                sealed.ciphertext,
            )
            await audit(
                conn, principal, "credential.create", str(credential_id), {"provider": provider}
            )
    except asyncpg.UniqueViolationError as exc:
        raise ApiProblem(409, "conflict", "A credential with that name already exists.") from exc
    return credential_id


async def list_credentials(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    """Metadata only: the sealed bytes are never selected."""
    async with db.tenant(org_id) as conn:
        rows = await conn.fetch(
            "SELECT id, name, provider, created_at FROM credentials ORDER BY created_at DESC"
        )
    return [{**dict(r), "id": str(r["id"])} for r in rows]


async def open_credential(db: Database, kms: Kms, org_id: UUID, credential_id: UUID) -> str:
    """Decrypt a credential for use inside the platform. Opens only for its own org and id."""
    async with db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            "SELECT kms_key_id, wrapped_dek, nonce, ciphertext FROM credentials WHERE id = $1",
            credential_id,
        )
    if row is None:
        raise not_found("credential")
    sealed = SealedSecret(row["kms_key_id"], row["wrapped_dek"], row["nonce"], row["ciphertext"])
    plain = await open_sealed(kms, sealed, org_id=str(org_id), credential_id=str(credential_id))
    return plain.decode()


async def delete_credential(db: Database, principal: Principal, credential_id: UUID) -> None:
    async with db.tenant(principal.org_id) as conn:
        done = await conn.execute("DELETE FROM credentials WHERE id = $1", credential_id)
        if done != "DELETE 1":
            raise not_found("credential")
        await audit(conn, principal, "credential.delete", str(credential_id), {})


# ------------------------------------------------------------------------ data sources
async def create_data_source(
    db: Database,
    principal: Principal,
    *,
    name: str,
    kind: str,
    credential_id: UUID | None,
    make_default: bool,
) -> UUID:
    try:
        async with db.tenant(principal.org_id) as conn:
            if make_default:
                await conn.execute("UPDATE data_sources SET is_default = false")
            has_default = await conn.fetchval("SELECT count(*) FROM data_sources WHERE is_default")
            source_id = await conn.fetchval(
                "INSERT INTO data_sources (org_id, name, kind, credential_id, is_default) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                principal.org_id,
                name.strip()[:80],
                kind,
                credential_id,
                make_default or has_default == 0,
            )
            await audit(conn, principal, "data_source.create", str(source_id), {"kind": kind})
    except asyncpg.UniqueViolationError as exc:
        raise ApiProblem(409, "conflict", "A data source with that name already exists.") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise ApiProblem(422, "invalid_request", "That credential does not exist.") from exc
    return UUID(str(source_id))


async def list_data_sources(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    async with db.tenant(org_id) as conn:
        sources = await conn.fetch(
            "SELECT id, name, kind, is_default, credential_id, created_at FROM data_sources "
            "ORDER BY created_at"
        )
        files = await conn.fetch(
            "SELECT data_source_id, symbol, n_rows, first_ts, last_ts "
            "FROM data_files ORDER BY symbol"
        )
    by_source: dict[UUID, list[dict[str, Any]]] = {}
    for f in files:
        by_source.setdefault(f["data_source_id"], []).append(
            {
                "symbol": f["symbol"],
                "n_rows": f["n_rows"],
                "first_ts": f["first_ts"].isoformat(),
                "last_ts": f["last_ts"].isoformat(),
            }
        )
    return [
        {
            "id": str(s["id"]),
            "name": s["name"],
            "kind": s["kind"],
            "is_default": s["is_default"],
            "credential_id": str(s["credential_id"]) if s["credential_id"] else None,
            "created_at": s["created_at"],
            "files": by_source.get(s["id"], []),
        }
        for s in sources
    ]


async def get_data_source(db: Database, org_id: UUID, source_id: UUID) -> dict[str, Any]:
    async with db.tenant(org_id) as conn:
        row = await conn.fetchrow("SELECT id, kind FROM data_sources WHERE id = $1", source_id)
    if row is None:
        raise not_found("data source")
    return dict(row)


async def default_data_source(db: Database, org_id: UUID) -> UUID | None:
    async with db.tenant(org_id) as conn:
        value = await conn.fetchval("SELECT id FROM data_sources WHERE is_default AND kind = 'csv'")
    return value  # type: ignore[no-any-return]


async def record_data_file(
    db: Database,
    principal: Principal,
    *,
    source_id: UUID,
    symbol: str,
    blob_key: str,
    n_rows: int,
    first_ts: Any,
    last_ts: Any,
    sha256: str,
) -> None:
    async with db.tenant(principal.org_id) as conn:
        await conn.execute(
            "INSERT INTO data_files (org_id, data_source_id, symbol, blob_key, n_rows, first_ts, "
            "last_ts, sha256) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            "ON CONFLICT (data_source_id, symbol) DO UPDATE SET blob_key = $4, n_rows = $5, "
            "first_ts = $6, last_ts = $7, sha256 = $8, uploaded_at = now()",
            principal.org_id,
            source_id,
            symbol,
            blob_key,
            n_rows,
            first_ts,
            last_ts,
            sha256,
        )
        await audit(conn, principal, "data_file.upload", f"{source_id}/{symbol}", {"rows": n_rows})


async def delete_data_source(db: Database, principal: Principal, source_id: UUID) -> None:
    async with db.tenant(principal.org_id) as conn:
        done = await conn.execute("DELETE FROM data_sources WHERE id = $1", source_id)
        if done != "DELETE 1":
            raise not_found("data source")
        await audit(conn, principal, "data_source.delete", str(source_id), {})
