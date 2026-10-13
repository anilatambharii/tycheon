"""Organisation erasure and export routes.

``POST /v1/org/erase`` only *schedules* an erasure (owner, re-authenticated); a scheduled job
carries it out after the grace period. ``GET /v1/org/export`` hands the data back first.

Proprietary: see ee/LICENSE.
"""

# NOTE: no ``from __future__ import annotations``: FastAPI reads the route annotations.
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from tycheon_cp import erasure, store
from tycheon_cp.app import ControlPlane, _Deps
from tycheon_cp.billing import Billing, StripeApi
from tycheon_cp.billing_routes import build_billing
from tycheon_cp.errors import ApiProblem
from tycheon_cp.export import build_export
from tycheon_cp.ratelimit import RateLimitedError


class EraseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_slug: str = Field(min_length=2, max_length=39)
    #: required for accounts that have a password; accounts that sign in with SSO must have signed
    #: in within the last 15 minutes instead
    password: str | None = Field(default=None, max_length=256)


def register_erasure(app: FastAPI, cp: ControlPlane) -> None:
    deps = _Deps(cp)
    Owner = Annotated[store.Principal, Depends(deps.session_principal("owner"))]  # noqa: N806
    Admin = Annotated[store.Principal, Depends(deps.session_principal("admin"))]  # noqa: N806
    router = APIRouter(prefix="/v1/org", tags=["account"])

    @router.post("/erase", status_code=202)
    async def request_erasure(body: EraseIn, who: Owner) -> dict[str, Any]:
        """Schedule permanent erasure of the organisation after the grace period.

        Needs the organisation's slug and the owner's password (or a fresh single-sign-on
        session). Owners and admins are notified, and any admin can cancel until it runs.
        Export your data first: ``GET /v1/org/export``.
        """
        return await erasure.request_erasure(
            cp, who, confirm_slug=body.confirm_slug, password=body.password
        )

    @router.get("/erasure")
    async def erasure_status(who: Admin) -> dict[str, Any]:
        return await erasure.status(cp.db, who.org_id)

    @router.post("/erase/cancel")
    async def cancel_erasure(who: Admin) -> dict[str, Any]:
        """Cancel a scheduled erasure. Any admin may; stopping is the safe direction."""
        return await erasure.cancel_erasure(cp, who)

    @router.get("/export")
    async def export(who: Owner, include_models: bool = False) -> FileResponse:
        """Download everything the organisation holds as one ZIP (secrets are never included)."""
        try:
            cp.limiter.allow(f"export:{who.org_id}", 1)
        except RateLimitedError as exc:
            raise ApiProblem(429, "rate_limited", "One export per minute.") from exc
        path = await build_export(cp.db, cp.blobs, who.org_id, include_models=include_models)
        async with cp.db.tenant(who.org_id) as conn:
            await store.audit(conn, who, "org.export", None, {"models": include_models})
        return FileResponse(
            path,
            media_type="application/zip",
            filename="tycheon-export.zip",
            background=BackgroundTask(Path(path).unlink, missing_ok=True),
        )

    app.include_router(router)


def billing_factory(cp: ControlPlane, api: StripeApi | None = None) -> "Billing | None":
    """The billing service, or ``None`` when Stripe is not configured."""
    service = cp.extras.get("billing")
    if service is None:
        try:
            service = cp.extras["billing"] = build_billing(cp, api)
        except Exception:
            return None
    return service
