"""Assembles the full API: the core routes plus every optional feature module.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tycheon_cp.app import ControlPlane, create_app
from tycheon_cp.billing_routes import register_billing
from tycheon_cp.sso import register_sso
from tycheon_ft.routes import register_finetune

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tycheon_cp.billing import StripeApi


def create_full_app(
    cp: ControlPlane, *, lifespan: Any | None = None, stripe_api: StripeApi | None = None
) -> FastAPI:
    app = create_app(cp, lifespan=lifespan)
    register_sso(app, cp)
    register_billing(app, cp, stripe_api)
    register_finetune(app, cp)
    return app
