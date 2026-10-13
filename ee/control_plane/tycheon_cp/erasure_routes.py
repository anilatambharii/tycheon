"""``POST /v1/org/erase``: the owner erases the organisation. Irreversible.

Proprietary: see ee/LICENSE.
"""

# NOTE: no ``from __future__ import annotations``: FastAPI reads the route annotations.
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from tycheon_cp import store
from tycheon_cp.app import ControlPlane, _Deps
from tycheon_cp.billing import Billing, StripeApi
from tycheon_cp.billing_routes import build_billing
from tycheon_cp.erasure import erase_org


class EraseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_slug: str = Field(min_length=2, max_length=39)


def register_erasure(app: FastAPI, cp: ControlPlane, api: StripeApi | None = None) -> None:
    deps = _Deps(cp)
    Owner = Annotated[store.Principal, Depends(deps.session_principal("owner"))]  # noqa: N806
    router = APIRouter(prefix="/v1/org", tags=["account"])

    def billing() -> Billing | None:
        service = cp.extras.get("billing")
        if service is None:
            try:
                service = cp.extras["billing"] = build_billing(cp, api)
            except Exception:
                return None
        return service

    @router.post("/erase")
    async def erase(body: EraseIn, who: Owner) -> dict[str, Any]:
        """Permanently delete the organisation, its users, keys, data, models and audit log."""
        return await erase_org(cp.db, cp.blobs, billing(), who, confirm_slug=body.confirm_slug)

    app.include_router(router)
