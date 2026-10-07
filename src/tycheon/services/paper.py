"""A paper (simulated) order blotter. Nothing here talks to a broker, and nothing ever will in v1.

AGENTS.md: no live brokerage execution in v1; paper and simulation only, and only through
Keelgate-governed tools. This is the in-memory ledger the governed ``propose_paper_trade``
tool writes to *after* a human approval.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from tycheon.services.schemas import PaperTradeOut

if TYPE_CHECKING:
    from tycheon.services.schemas import PaperTradeIn


class PaperBlotter:
    def __init__(self) -> None:
        self._orders: dict[str, list[PaperTradeOut]] = {}
        self._lock = threading.Lock()

    def place(self, tenant_id: str, order: PaperTradeIn) -> PaperTradeOut:
        with self._lock:
            book = self._orders.setdefault(tenant_id, [])
            filled = PaperTradeOut(
                order_id=f"paper-{len(book) + 1}",
                status="filled-paper",
                symbol=order.symbol,
                side=order.side,
                notional=order.notional,
            )
            book.append(filled)
            return filled

    def orders(self, tenant_id: str) -> list[PaperTradeOut]:
        with self._lock:
            return list(self._orders.get(tenant_id, ()))
