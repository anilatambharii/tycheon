"""Fine-tune jobs, the per-tenant model registry and per-tenant routing, all inside one tenant.

A model row can only become ``promoted`` through :func:`record_gate_outcome`, which stores the
gate's evidence in the same statement, and the database itself refuses ``promoted`` without a
passing gate (a CHECK constraint), so a bug here cannot promote a model that failed.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tycheon_cp import store
from tycheon_cp.errors import ApiProblem, not_found

if TYPE_CHECKING:
    from tycheon_cp.db import Database
    from tycheon_ft.dataset import FineTuneConfig
    from tycheon_ft.gate import GateResult

BUILTIN_MODELS = ("random-walk", "drift", "garch", "kronos-mini")
_FT = re.compile(r"^ft:([0-9a-f-]{36})$")
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$")


def _job(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["id"] = str(out["id"])
    out["data_source_id"] = str(out["data_source_id"])
    out["model_id"] = str(out["model_id"]) if out.get("model_id") else None
    out["params"] = json.loads(out["params"]) if isinstance(out["params"], str) else out["params"]
    out["progress"] = (
        json.loads(out["progress"]) if isinstance(out["progress"], str) else out["progress"]
    )
    out["gpu_seconds"] = float(out["gpu_seconds"])
    out["symbols"] = list(out["symbols"])
    out.pop("worker", None)
    out.pop("created_by", None)
    return out


_JOB_COLUMNS = (
    "id, data_source_id, symbols, params, gpu_pool, status, error, gpu_seconds, model_id, "
    "progress, created_at, started_at, finished_at, org_id"
)


async def create_job(
    db: Database,
    principal: store.Principal,
    *,
    source_id: UUID,
    config: FineTuneConfig,
    gpu_pool: str,
) -> dict[str, Any]:
    async with db.tenant(principal.org_id) as conn:
        found = {
            r["symbol"]: r["n_rows"]
            for r in await conn.fetch(
                "SELECT symbol, n_rows FROM data_files WHERE data_source_id = $1", source_id
            )
        }
        if not await conn.fetchval("SELECT 1 FROM data_sources WHERE id = $1", source_id):
            raise not_found("data source")
        missing = [s for s in config.symbols if s not in found]
        if missing:
            raise ApiProblem(
                422, "invalid_request", f"No uploaded data for: {', '.join(sorted(missing))}."
            )
        row = await conn.fetchrow(
            f"INSERT INTO finetune_jobs (org_id, data_source_id, symbols, params, gpu_pool, "  # noqa: S608
            f"created_by) VALUES ($1, $2, $3, $4::jsonb, $5, $6) RETURNING {_JOB_COLUMNS}",
            principal.org_id,
            source_id,
            config.symbols,
            config.model_dump_json(),
            gpu_pool,
            principal.user_id,
        )
        await store.audit(
            conn, principal, "finetune.submit", str(row["id"]), {"symbols": config.symbols}
        )
    return _job(row)


async def n_rows(db: Database, org_id: UUID, source_id: UUID) -> dict[str, int]:
    async with db.tenant(org_id) as conn:
        rows = await conn.fetch(
            "SELECT symbol, n_rows FROM data_files WHERE data_source_id = $1", source_id
        )
    return {r["symbol"]: int(r["n_rows"]) for r in rows}


async def list_jobs(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    async with db.tenant(org_id) as conn:
        rows = await conn.fetch(
            f"SELECT {_JOB_COLUMNS} FROM finetune_jobs ORDER BY created_at DESC LIMIT 200"  # noqa: S608
        )
    return [_job(r) for r in rows]


async def get_job(db: Database, org_id: UUID, job_id: UUID) -> dict[str, Any]:
    async with db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            f"SELECT {_JOB_COLUMNS} FROM finetune_jobs WHERE id = $1",  # noqa: S608
            job_id,
        )
    if row is None:
        raise not_found("fine-tune job")
    return _job(row)


async def cancel_job(db: Database, principal: store.Principal, job_id: UUID) -> dict[str, Any]:
    async with db.tenant(principal.org_id) as conn:
        done = await conn.execute(
            "UPDATE finetune_jobs SET status = 'cancelled', finished_at = now() "
            "WHERE id = $1 AND status IN ('queued', 'running')",
            job_id,
        )
        if done != "UPDATE 1":
            exists = await conn.fetchval("SELECT 1 FROM finetune_jobs WHERE id = $1", job_id)
            if not exists:
                raise not_found("fine-tune job")
            raise ApiProblem(409, "conflict", "That job has already finished.")
        await store.audit(conn, principal, "finetune.cancel", str(job_id), {})
    return await get_job(db, principal.org_id, job_id)


async def job_status(db: Database, org_id: UUID, job_id: UUID) -> str | None:
    async with db.tenant(org_id) as conn:
        value = await conn.fetchval("SELECT status FROM finetune_jobs WHERE id = $1", job_id)
    return None if value is None else str(value)


# ---------------------------------------------------------------------------- models
_MODEL_COLUMNS = (
    "id, name, base_model, status, artifact_key, artifact_sha256, job_id, metrics, gate, "
    "created_at, promoted_at"
)


def _model(row: Any, *, internal: bool = False) -> dict[str, Any]:
    out = dict(row)
    out["id"] = str(out["id"])
    out["job_id"] = str(out["job_id"]) if out.get("job_id") else None
    for key in ("metrics", "gate"):
        if isinstance(out[key], str):
            out[key] = json.loads(out[key])
    if not internal:
        out.pop("artifact_key", None)
        out.pop("artifact_sha256", None)
    return out


async def add_candidate(
    db: Database,
    org_id: UUID,
    *,
    model_id: UUID,
    name: str,
    base_model: str,
    artifact_key: str,
    sha256: str,
    job_id: UUID,
    metrics: dict[str, Any],
) -> None:
    async with db.tenant(org_id) as conn:
        await conn.execute(
            "INSERT INTO models (id, org_id, name, base_model, status, artifact_key, "
            "artifact_sha256, job_id, metrics) VALUES ($1,$2,$3,$4,'candidate',$5,$6,$7,$8::jsonb)",
            model_id,
            org_id,
            name,
            base_model,
            artifact_key,
            sha256,
            job_id,
            json.dumps(metrics),
        )


async def record_gate_outcome(db: Database, org_id: UUID, model_id: UUID, gate: GateResult) -> str:
    """Store the gate's evidence and move the candidate to ``promoted`` or ``rejected``."""
    status = "promoted" if gate.passed else "rejected"
    async with db.tenant(org_id) as conn:
        done = await conn.execute(
            "UPDATE models SET status = $2, gate = $3::jsonb, "
            "promoted_at = CASE WHEN $2 = 'promoted' THEN now() END "
            "WHERE id = $1 AND status = 'candidate'",
            model_id,
            status,
            json.dumps(gate.to_dict()),
        )
        if done != "UPDATE 1":
            raise ApiProblem(409, "conflict", "Only a candidate model can be judged.")
        await conn.execute(
            "INSERT INTO audit_log (org_id, actor, action, target, details) "
            "VALUES ($1, 'finetune-worker', $2, $3, $4::jsonb)",
            org_id,
            f"model.{status}",
            str(model_id),
            json.dumps({"reasons": gate.reasons}),
        )
    return status


async def list_models(db: Database, org_id: UUID) -> list[dict[str, Any]]:
    async with db.tenant(org_id) as conn:
        rows = await conn.fetch(
            f"SELECT {_MODEL_COLUMNS} FROM models ORDER BY created_at DESC"  # noqa: S608
        )
    return [_model(r) for r in rows]


async def get_model(
    db: Database, org_id: UUID, model_id: UUID, *, internal: bool = False
) -> dict[str, Any]:
    async with db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            f"SELECT {_MODEL_COLUMNS} FROM models WHERE id = $1",  # noqa: S608
            model_id,
        )
    if row is None:
        raise not_found("model")
    return _model(row, internal=internal)


async def archive_model(db: Database, principal: store.Principal, model_id: UUID) -> None:
    async with db.tenant(principal.org_id) as conn:
        done = await conn.execute(
            "UPDATE models SET status = 'archived' WHERE id = $1 AND status <> 'archived'",
            model_id,
        )
        if done != "UPDATE 1":
            raise not_found("model")
        await store.audit(conn, principal, "model.archive", str(model_id), {})


# --------------------------------------------------------------------------- routing
async def get_routing(db: Database, org_id: UUID) -> dict[str, Any]:
    async with db.tenant(org_id) as conn:
        value = await conn.fetchval("SELECT config FROM routing_configs")
    if value is None:
        return {"default_model": "random-walk", "overrides": {}}
    return json.loads(value) if isinstance(value, str) else dict(value)


async def validate_routing(db: Database, org_id: UUID, config: dict[str, Any]) -> None:
    """Every model a route names must be a built-in or one of *this* tenant's promoted models."""
    async with db.tenant(org_id) as conn:
        promoted = {
            str(r["id"])
            for r in await conn.fetch("SELECT id FROM models WHERE status = 'promoted'")
        }

    def check(name: Any) -> None:
        if name in BUILTIN_MODELS:
            return
        match = _FT.fullmatch(str(name))
        if match and match.group(1) in promoted:
            return
        raise ApiProblem(
            422,
            "invalid_request",
            f"{name!r} is not a built-in model or one of your promoted models.",
        )

    check(config.get("default_model"))
    overrides = config.get("overrides", {})
    if not isinstance(overrides, dict) or len(overrides) > 100:
        raise ApiProblem(422, "invalid_request", "overrides must be a map of at most 100 symbols.")
    for symbol, model in overrides.items():
        if not _SYMBOL.fullmatch(str(symbol)):
            raise ApiProblem(422, "invalid_request", "an override names an invalid symbol.")
        check(model)


async def set_routing(
    db: Database, principal: store.Principal, config: dict[str, Any]
) -> dict[str, Any]:
    await validate_routing(db, principal.org_id, config)
    clean = {"default_model": config["default_model"], "overrides": config.get("overrides", {})}
    async with db.tenant(principal.org_id) as conn:
        await conn.execute(
            "INSERT INTO routing_configs (org_id, config) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (org_id) DO UPDATE SET config = $2::jsonb, updated_at = now()",
            principal.org_id,
            json.dumps(clean),
        )
        await store.audit(conn, principal, "routing.set", None, clean)
    return clean


def parse_ft(name: str) -> UUID | None:
    match = _FT.fullmatch(name)
    return UUID(match.group(1)) if match else None
