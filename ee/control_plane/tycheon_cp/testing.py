"""Test support: create organisations through the same signup path the API uses.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tycheon_cp.db import Database


@dataclass(frozen=True)
class Org:
    org_id: uuid.UUID
    user_id: uuid.UUID
    slug: str
    email: str


async def make_org(database: Database, name: str = "Acme") -> Org:
    """A new organisation through the same SECURITY DEFINER signup the API uses."""
    tag = uuid.uuid4().hex[:10]
    slug, email = f"org-{tag}", f"owner-{tag}@example.com"
    async with database.anonymous() as conn:
        row = await conn.fetchrow("SELECT * FROM cp_signup($1, $2, $3, $4)", slug, name, email, "x")
    if row is None:
        raise RuntimeError("signup returned nothing")
    return Org(row["org_id"], row["user_id"], slug, email)
