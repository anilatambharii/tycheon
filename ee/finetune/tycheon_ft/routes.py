"""Fine-tuning, model registry and routing endpoints (Enterprise plans).

Proprietary: see ee/LICENSE.
"""

# NOTE: no ``from __future__ import annotations``: FastAPI reads the route annotations.
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from tycheon_cp import store
from tycheon_cp.app import ControlPlane, _Deps
from tycheon_cp.errors import ApiProblem
from tycheon_cp.metering import FeatureNotIncludedError, QuotaExceededError
from tycheon_ft import registry
from tycheon_ft.dataset import DatasetError, FineTuneConfig, split_for

MAX_ACTIVE_JOBS = 2


class FineTuneIn(FineTuneConfig):
    data_source_id: UUID


class RoutingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_model: str = Field(max_length=80)
    overrides: dict[str, str] = Field(default_factory=dict, max_length=100)


def register_finetune(app: FastAPI, cp: ControlPlane) -> None:
    deps = _Deps(cp)
    Viewer = Annotated[store.Principal, Depends(deps.session_principal("viewer"))]  # noqa: N806
    Admin = Annotated[store.Principal, Depends(deps.session_principal("admin"))]  # noqa: N806
    router = APIRouter(prefix="/v1", tags=["finetune"])

    async def require_finetune(who: store.Principal) -> store.OrgContext:
        org = await store.load_org(cp.db, who.org_id, cp.plans)
        try:
            cp.meter.require_feature(org.plan, "finetune")
        except FeatureNotIncludedError as exc:
            raise ApiProblem(
                403, "plan_feature", f"The {org.plan.name} plan does not include fine-tuning."
            ) from exc
        return org

    @router.post("/finetune/jobs", status_code=202)
    async def submit(body: FineTuneIn, who: Admin) -> dict[str, Any]:
        """Queue a per-tenant Kronos fine-tune. It runs on a worker; poll the job for status."""
        org = await require_finetune(who)
        try:
            await cp.meter.ensure_headroom(
                org.org_id, org.plan, "gpu_seconds", override=org.override
            )
        except QuotaExceededError as exc:
            raise ApiProblem(429, "quota_exceeded", "Your GPU-seconds quota is used up.") from exc
        active = [
            j
            for j in await registry.list_jobs(cp.db, who.org_id)
            if j["status"] in ("queued", "running")
        ]
        if len(active) >= MAX_ACTIVE_JOBS:
            raise ApiProblem(429, "too_many_jobs", "Wait for a running fine-tune to finish first.")
        config = FineTuneConfig.model_validate(body.model_dump(exclude={"data_source_id"}))
        rows = await registry.n_rows(cp.db, who.org_id, body.data_source_id)
        try:
            for symbol in config.symbols:
                if symbol in rows:
                    split_for(symbol, rows[symbol], config)
        except DatasetError as exc:
            raise ApiProblem(422, "invalid_data", str(exc)) from exc
        pool = (
            f"dedicated-{who.org_id}"
            if cp.settings.dedicated_gpu_pools and org.plan.has("dedicated_gpu")
            else "shared"
        )
        return await registry.create_job(
            cp.db, who, source_id=body.data_source_id, config=config, gpu_pool=pool
        )

    @router.get("/finetune/jobs")
    async def jobs(who: Viewer) -> list[dict[str, Any]]:
        return await registry.list_jobs(cp.db, who.org_id)

    @router.get("/finetune/jobs/{job_id}")
    async def job(job_id: UUID, who: Viewer) -> dict[str, Any]:
        return await registry.get_job(cp.db, who.org_id, job_id)

    @router.post("/finetune/jobs/{job_id}/cancel")
    async def cancel(job_id: UUID, who: Admin) -> dict[str, Any]:
        return await registry.cancel_job(cp.db, who, job_id)

    @router.get("/models")
    async def models(who: Viewer) -> list[dict[str, Any]]:
        return await registry.list_models(cp.db, who.org_id)

    @router.get("/models/{model_id}")
    async def model(model_id: UUID, who: Viewer) -> dict[str, Any]:
        return await registry.get_model(cp.db, who.org_id, model_id)

    @router.post("/models/{model_id}/archive", status_code=204)
    async def archive(model_id: UUID, who: Admin) -> Response:
        await registry.archive_model(cp.db, who, model_id)
        return Response(status_code=204)

    @router.get("/routing")
    async def routing(who: Viewer) -> dict[str, Any]:
        return await registry.get_routing(cp.db, who.org_id)

    @router.put("/routing")
    async def set_routing(body: RoutingIn, who: Admin) -> dict[str, Any]:
        await require_finetune(who)
        return await registry.set_routing(cp.db, who, body.model_dump())

    app.include_router(router)
