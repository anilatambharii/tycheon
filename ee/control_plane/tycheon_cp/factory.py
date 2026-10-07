"""Assembles the full API: the core routes plus every optional feature module.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tycheon_cp.app import ControlPlane, create_app
from tycheon_cp.sso import register_sso

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_full_app(cp: ControlPlane, *, lifespan: Any | None = None) -> FastAPI:
    app = create_app(cp, lifespan=lifespan)
    register_sso(app, cp)
    return app
