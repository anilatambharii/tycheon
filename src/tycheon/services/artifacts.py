"""A small, bounded, tenant-scoped store for generated artifacts (full risk reports).

A governed tool returns a compact typed summary and a ``report_id``; the full JSON and HTML
report sits here until it is fetched. Reads are keyed by tenant, so one tenant can never fetch
another's report by guessing an id.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tycheon.risk import RiskReport


class ArtifactStore:
    def __init__(self, max_items: int = 64) -> None:
        self._max = max_items
        self._items: OrderedDict[tuple[str, str], RiskReport] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, tenant_id: str, report_id: str, report: RiskReport) -> None:
        with self._lock:
            self._items[(tenant_id, report_id)] = report
            self._items.move_to_end((tenant_id, report_id))
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def get(self, tenant_id: str, report_id: str) -> RiskReport | None:
        with self._lock:
            return self._items.get((tenant_id, report_id))


#: Process-wide default. Tests and deployments may build their own.
ARTIFACTS = ArtifactStore()
