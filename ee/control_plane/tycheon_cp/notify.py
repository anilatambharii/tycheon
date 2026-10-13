"""Notifying the people who can act on an erasure.

A scheduled erasure that nobody hears about is a trap, so the owners and admins of the
organisation are told when it is requested, cancelled and carried out. Delivery is a plug-in
point: the control plane has no mail transport of its own, so the default only logs *how many*
people would have been told (never their addresses). A deployment must supply a real notifier
(SES, SMTP) under ``cp.extras["notifier"]`` before taking customers; until then the same facts
are visible to every admin through ``GET /v1/org/erasure`` and the audit log.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("tycheon_cp.notify")


class Notifier(Protocol):
    async def send(self, recipients: list[str], subject: str, body: str) -> None: ...


class LogNotifier:
    """Records that a notice was due. Not a delivery mechanism."""

    async def send(self, recipients: list[str], subject: str, body: str) -> None:  # noqa: ARG002
        log.warning(
            "notice not delivered (no mail transport configured): %d recipient(s)", len(recipients)
        )
