"""Export everything an organisation holds, before it decides to erase it.

A processor must be able to hand data back (GDPR Art. 28(3)(g) "delete or return"), and a customer
with their own record-keeping duties needs their reports, data and audit trail first. The export is
one ZIP of JSON files plus the uploaded CSVs (and, on request, the fine-tuned model files).

Never included: password hashes, API-key secret hashes, stored credential secrets (names only), the
Stripe customer and subscription ids.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tycheon.models.base import DISCLAIMER
from tycheon_cp.errors import ApiProblem

if TYPE_CHECKING:
    from uuid import UUID

    from tycheon_cp.blob import BlobStore
    from tycheon_cp.db import Database

MAX_EXPORT_BYTES = 2 * 1024**3

# table -> the columns that may leave the system (an allowlist: new columns are opt-in)
_TABLES: dict[str, str] = {
    "organisation": "SELECT id, slug, name, plan, created_at FROM orgs",
    "users": "SELECT id, email, role, oidc_subject, created_at, disabled_at FROM users ORDER BY created_at",  # noqa: E501
    "api_keys": "SELECT id, name, prefix, scopes, created_by, created_at, last_used_at, revoked_at FROM api_keys",  # noqa: E501  # pragma: allowlist secret
    "credentials": "SELECT id, name, provider, created_at FROM credentials",
    "data_sources": "SELECT id, name, kind, is_default, created_at FROM data_sources",
    "data_files": "SELECT id, data_source_id, symbol, n_rows, first_ts, last_ts, sha256, uploaded_at FROM data_files",  # noqa: E501
    "usage_counters": "SELECT period, kind, used FROM usage_counters ORDER BY period",
    "usage_events": "SELECT id, kind, quantity, occurred_at FROM usage_events ORDER BY id",
    "subscription": "SELECT plan, status, trial_end, current_period_end FROM subscriptions",
    "one_off_invoices": "SELECT id, description, amount_cents, currency, status, created_at FROM oneoff_invoices",  # noqa: E501
    "sso_provider": "SELECT issuer, client_id, allowed_domains, default_role FROM oidc_providers",
    "finetune_jobs": "SELECT id, status, symbols, params, gpu_pool, gpu_seconds, error, created_at, started_at, finished_at FROM finetune_jobs",  # noqa: E501
    "models": "SELECT id, name, base_model, status, metrics, gate, created_at, promoted_at FROM models",  # noqa: E501
    "routing": "SELECT config, updated_at FROM routing_configs",
    "audit_log": "SELECT id, actor, action, target, details, occurred_at FROM audit_log ORDER BY id",  # noqa: E501
}


def _dump(value: Any) -> bytes:
    return json.dumps(value, indent=2, default=str, ensure_ascii=False).encode("utf-8")


class _Zip:
    def __init__(self, path: Path) -> None:
        self.handle = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
        self.path = path
        self.names: list[str] = []

    def add(self, name: str, data: bytes) -> None:
        self.handle.writestr(name, data)
        self.names.append(name)
        if self.path.stat().st_size > MAX_EXPORT_BYTES:
            raise ApiProblem(413, "export_too_large", "The export is too large to build in one go.")


async def build_export(
    db: Database, blobs: BlobStore, org_id: UUID, *, include_models: bool = False
) -> Path:
    """Write the export to a temporary file and return its path. The caller deletes it."""
    handle = tempfile.NamedTemporaryFile(prefix="tycheon-export-", suffix=".zip", delete=False)  # noqa: SIM115
    handle.close()
    out = _Zip(Path(handle.name))
    try:
        async with db.tenant(org_id) as conn:
            tables = {
                name: [dict(r) for r in await conn.fetch(sql)] for name, sql in _TABLES.items()
            }
            files = await conn.fetch("SELECT data_source_id, symbol, blob_key FROM data_files")
            model_keys = await conn.fetch("SELECT id, artifact_key FROM models")
        for name, rows in tables.items():
            out.add(f"{name}.json", _dump(rows))
        for row in files:
            out.add(
                f"files/{row['data_source_id']}/{row['symbol']}.csv", blobs.get(row["blob_key"])
            )
        if include_models:
            for model in model_keys:
                directory = blobs.directory(model["artifact_key"])
                for path in sorted(p for p in directory.rglob("*") if p.is_file()):
                    rel = path.relative_to(directory).as_posix()
                    out.add(f"models/{model['id']}/{rel}", path.read_bytes())
        manifest = {
            "disclaimer": DISCLAIMER,
            "generated_at": datetime.now(UTC).isoformat(),
            "organisation_id": str(org_id),
            "includes_model_files": include_models,
            "contents": sorted(out.names),
            "excluded": [
                "password hashes",
                "API key secrets",
                "stored credential secrets (names only)",
                "Stripe customer and subscription ids",
            ],
        }
        out.add("MANIFEST.json", _dump(manifest))
    except BaseException:
        out.handle.close()
        out.path.unlink(missing_ok=True)
        raise
    out.handle.close()
    return out.path
