"""Prometheus metrics for the control plane (what the SLO alerts in docs/ops are written against).

Design rules:

* **No tenant identifiers, ever.** Labels are route templates, status classes, meter kinds, model
  families and plan keys: low cardinality, and nothing a customer could be identified by.
* **Route templates, not paths**: ``/v1/report/{report_id}``, never ``/v1/report/abc123``.
* **Private by default.** ``/metrics`` answers 404 unless ``TYCHEON_CP_METRICS_TOKEN`` is set, and
  then only to a request that presents it (constant-time compare). Scrape it from inside the
  cluster; the ingress must not route it.

Calibration quality is observed *at serve time*: the share of forecasts that are calibrated,
stale or uncalibrated, and the gap between each interval's nominal coverage and its holdout
coverage. That is a proxy for calibration decay, not a measurement of it: true decay needs
realised outcomes, which a nightly evaluation job must supply (documented in docs/ops/slos.md).

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hmac
import time
from typing import TYPE_CHECKING, Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tycheon_cp.db import Database

LATENCY_BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
GAP_BUCKETS = (0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.2, 0.5)
QUEUE_TTL_SECONDS = 15.0


class Metrics:
    """One registry per control plane (so tests can build many without name clashes)."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter(
            "tycheon_http_requests_total",
            "HTTP requests",
            ["route", "method", "status_class"],
            registry=r,
        )
        self.latency = Histogram(
            "tycheon_http_request_duration_seconds",
            "HTTP request latency",
            ["route", "method"],
            buckets=LATENCY_BUCKETS,
            registry=r,
        )
        self.usage = Counter(
            "tycheon_usage_committed_total",
            "Metered usage committed, by meter",
            ["kind"],
            registry=r,
        )
        self.gpu_seconds = Counter(
            "tycheon_gpu_seconds_total", "Fine-tune compute seconds recorded", registry=r
        )
        self.refusals = Counter(
            "tycheon_refusals_total",
            "Requests refused before any work, by reason",
            ["reason"],
            registry=r,
        )
        self.calibration = Counter(
            "tycheon_forecast_calibration_total",
            "Forecasts served, by calibration status",
            ["status", "model_family"],
            registry=r,
        )
        self.coverage_gap = Histogram(
            "tycheon_calibration_coverage_gap",
            "|nominal - holdout coverage| per served interval",
            ["model_family"],
            buckets=GAP_BUCKETS,
            registry=r,
        )
        self.in_flight = Gauge(
            "tycheon_http_requests_in_flight", "Requests currently being handled", registry=r
        )
        self._queue_cache: tuple[float, list[tuple[str, int, int, float]]] = (0.0, [])
        self._queue_source: Callable[[], Awaitable[list[tuple[str, int, int, float]]]] | None = None
        r.register(_QueueCollector(self))

    # ----------------------------------------------------------------------- recording
    def observe_request(self, route: str, method: str, status: int, seconds: float) -> None:
        self.requests.labels(route, method, f"{status // 100}xx").inc()
        self.latency.labels(route, method).observe(seconds)

    def observe_forecast(self, output: dict[str, Any]) -> None:
        """Record calibration quality from a forecast response (no symbols, no tenants)."""
        family = _family(str(output.get("model_id", "unknown")))
        self.calibration.labels(str(output.get("calibration_status", "unknown")), family).inc()
        evidence = output.get("calibration") or {}
        for level, held in (evidence.get("holdout_coverage") or {}).items():
            try:
                self.coverage_gap.labels(family).observe(abs(float(level) - float(held)))
            except (TypeError, ValueError):
                continue

    # ------------------------------------------------------------------------- scraping
    def set_queue_source(
        self, source: Callable[[], Awaitable[list[tuple[str, int, int, float]]]]
    ) -> None:
        self._queue_source = source

    async def refresh_queue(self, now: float | None = None) -> None:
        if self._queue_source is None:
            return
        moment = time.monotonic() if now is None else now
        if moment - self._queue_cache[0] < QUEUE_TTL_SECONDS:
            return
        try:
            self._queue_cache = (moment, await self._queue_source())
        except Exception:  # a database blip must not break the scrape of everything else
            self.refusals.labels("metrics_queue_unavailable").inc()

    def render(self) -> bytes:
        return generate_latest(self.registry)


def _family(model_id: str) -> str:
    """A bounded label from a model id: ``ft:<uuid>`` and the like collapse to their family."""
    if model_id.startswith("ft:"):
        return "fine-tuned"
    head = model_id.split("+", maxsplit=1)[0].split(":", maxsplit=1)[0]
    return (
        head
        if head in {"random-walk", "drift", "garch", "kronos-mini", "seasonal-naive"}
        else "other"
    )


class _QueueCollector:
    """Exposes the fine-tune queue as gauges, read from the last refresh."""

    def __init__(self, metrics: Metrics) -> None:
        self._m = metrics

    def collect(self) -> Any:
        rows = self._m._queue_cache[1]
        for name, doc, index in (
            ("tycheon_finetune_jobs_queued", "Fine-tune jobs waiting, by pool", 1),
            ("tycheon_finetune_jobs_running", "Fine-tune jobs running, by pool", 2),
            ("tycheon_finetune_oldest_queued_seconds", "Age of the oldest waiting job, by pool", 3),
        ):
            family = GaugeMetricFamily(name, doc, labels=["pool"])
            for row in rows:
                family.add_metric([_pool_label(row[0])], float(row[index]))
            yield family


def _pool_label(pool: str) -> str:
    """``dedicated-<org id>`` would identify a tenant: collapse dedicated pools to one label."""
    return "dedicated" if pool.startswith("dedicated-") else pool


async def queue_stats(db: Database) -> list[tuple[str, int, int, float]]:
    async with db.anonymous() as conn:
        rows = await conn.fetch("SELECT * FROM cp_finetune_queue_stats()")
    merged: dict[str, tuple[int, int, float]] = {}
    for r in rows:
        key = _pool_label(r["gpu_pool"])
        q, run, age = merged.get(key, (0, 0, 0.0))
        merged[key] = (
            q + int(r["queued"]),
            run + int(r["running"]),
            max(age, float(r["oldest_queued_seconds"])),
        )
    return [(k, *v) for k, v in merged.items()]


def authorised(presented: str | None, token: str | None) -> bool:
    """Constant-time bearer check; with no token configured nothing is authorised."""
    if not token or not presented:
        return False
    return hmac.compare_digest(presented.encode(), token.encode())
