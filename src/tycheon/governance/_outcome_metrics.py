"""Tycheon's outcome metrics in the shape of Keelgate's ``OutcomeMetric`` protocol.

Keelgate calls ``compute(records) -> MetricResult`` over a batch of
:class:`~keelgate.evals.OutcomeRecord`. Tycheon's metrics work on a
:class:`~tycheon.calibration.scores.ScoreSet`, so each adapter here rebuilds one from the
records and calls the matching class in :mod:`tycheon.metrics`. No metric is computed in this
module.

**Record format.** One record is one forecast from one origin, in log-return space relative to
the origin's last close. :func:`records_from_scores` writes it:

* ``metadata``: ``schema`` (:data:`RECORD_SCHEMA`), ``model_id``, ``origin_time`` (ISO 8601
  with a timezone) and ``stride`` (bars between origins, which sets the Diebold-Mariano lags);
* ``predicted``: ``levels``, ``quantiles`` (``levels`` x ``horizon``), optionally ``samples``
  (``paths`` x ``horizon``) and, for Diebold-Mariano, ``benchmark``: the random walk's
  ``quantiles`` and optional ``samples`` for the same origin;
* ``realized``: ``log_returns`` (``horizon`` values).

**What a batch may contain.** Records without the Tycheon ``schema`` are ignored. A batch with
none of Tycheon's records (Keelgate's built-in sample data, for one) gives ``n=0`` and an
infinite value: every metric here is lower-is-better, so a baseline comparison treats "no data"
as a regression and not as a pass. Tycheon records that are malformed, mix models, horizons or
levels, or cannot support the metric raise ``ValueError``, which Keelgate reports as a failure.

**Coverage.** Coverage is best when it equals the nominal level, but Keelgate compares values in
one direction only. The value reported here is therefore the gap to nominal; the achieved
coverage is in ``details["coverage"]``.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from keelgate.evals import MetricResult, OutcomeRecord

from tycheon import metrics as core
from tycheon.calibration.scores import ScoreSet

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: ``metadata["schema"]`` of a record these metrics read.
RECORD_SCHEMA = "tycheon.forecast/1"


def records_from_scores(model: ScoreSet, benchmark: ScoreSet | None = None) -> list[OutcomeRecord]:
    """One :class:`OutcomeRecord` per forecast origin of ``model``.

    ``benchmark`` is the random walk's record at the same origins. Without it the
    Diebold-Mariano metric cannot be computed from these records.
    """
    if benchmark is not None and (
        benchmark.horizon != model.horizon
        or benchmark.levels != model.levels
        or not benchmark.origin_times.equals(model.origin_times)
    ):
        raise ValueError("model and benchmark must share origins, horizon and levels")
    records = []
    for i, origin in enumerate(model.origin_times):
        predicted: dict[str, Any] = {"levels": list(model.levels), **_forecast(model, i)}
        if benchmark is not None:
            predicted["benchmark"] = _forecast(benchmark, i)
        records.append(
            OutcomeRecord(
                record_id=f"{model.model_id}-{i}",
                predicted=predicted,
                realized={"log_returns": model.realized[i].tolist()},
                metadata={
                    "schema": RECORD_SCHEMA,
                    "model_id": model.model_id,
                    "origin_time": origin.isoformat(),
                    "stride": model.stride,
                },
            )
        )
    return records


def _forecast(scores: ScoreSet, i: int) -> dict[str, Any]:
    out: dict[str, Any] = {"quantiles": scores.base_logq[i].tolist()}
    if scores.samples is not None:
        out["samples"] = scores.samples[i].tolist()
    return out


def _scores(records: Sequence[OutcomeRecord], *, benchmark: bool = False) -> ScoreSet:
    """Rebuild the model's (or the benchmark's) ``ScoreSet`` from Tycheon records."""
    try:
        origins = pd.DatetimeIndex([pd.Timestamp(r.metadata["origin_time"]) for r in records])
        if origins.tz is None:
            raise ValueError("origin_time needs a timezone")
        shared = {
            (str(r.metadata["model_id"]), int(r.metadata["stride"]), tuple(r.predicted["levels"]))
            for r in records
        }
        if len(shared) != 1:
            raise ValueError("records in one batch must share model_id, stride and levels")
        ((model_id, stride, levels),) = shared
        forecasts = [r.predicted["benchmark"] if benchmark else r.predicted for r in records]
        quantiles = np.asarray([f["quantiles"] for f in forecasts], dtype=np.float64)
        realized = np.asarray([r.realized["log_returns"] for r in records], dtype=np.float64)
        with_samples = sum("samples" in f for f in forecasts)
        if with_samples not in (0, len(forecasts)):
            raise ValueError("either every record has sample paths or none does")
        samples = (
            np.asarray([f["samples"] for f in forecasts], dtype=np.float64)
            if with_samples
            else None
        )
    except KeyError as exc:
        raise ValueError(f"a Tycheon outcome record is missing {exc}") from exc
    if realized.ndim != 2 or not np.isfinite(realized).all() or not np.isfinite(quantiles).all():
        raise ValueError("realized and quantiles must be finite, with one horizon for all records")
    if samples is not None and not np.isfinite(samples).all():
        raise ValueError("sample paths must be finite")
    order = np.argsort(origins.asi8, kind="stable")  # DM autocovariances need time order
    return ScoreSet(
        model_id="random-walk" if benchmark else model_id,
        horizon=realized.shape[1],
        levels=tuple(float(level) for level in levels),
        origin_times=origins[order],
        # Not read by any metric: this ScoreSet is never used to fit a calibrator.
        outcome_times=np.zeros(realized.shape, dtype=np.int64),
        base_logq=quantiles[order],
        realized=realized[order],
        samples=None if samples is None else samples[order],
        n_samples=0 if samples is None else samples.shape[1],
        stride=stride,
    )


class _Adapter:
    """Shared handling of the batch; subclasses name a metric and call it."""

    name: str
    higher_is_better = False

    def compute(self, records: Sequence[OutcomeRecord]) -> MetricResult:
        ours = [r for r in records if r.metadata.get("schema") == RECORD_SCHEMA]
        if not ours:
            return MetricResult(
                self.name,
                math.inf,
                0,
                self.higher_is_better,
                {"reason": "no Tycheon forecast records in the batch"},
            )
        value, details = self._value(ours)
        return MetricResult(self.name, value, len(ours), self.higher_is_better, dict(details))

    def _value(self, records: Sequence[OutcomeRecord]) -> tuple[float, Mapping[str, object]]:
        raise NotImplementedError


def _required(result: core.MetricResult) -> tuple[float, Mapping[str, object]]:
    if result.value is None:
        raise ValueError(f"{result.name} cannot be computed on these records: {result.details}")
    return result.value, result.details


class CRPSOutcome(_Adapter):
    """Mean CRPS of the horizon-end return."""

    name = "crps"

    def _value(self, records: Sequence[OutcomeRecord]) -> tuple[float, Mapping[str, object]]:
        return _required(core.CRPS().compute(_scores(records)))


class CoverageGapOutcome(_Adapter):
    """Distance between achieved and nominal coverage of the central interval."""

    def __init__(self, level: float = 0.9) -> None:
        self._metric = core.IntervalCoverage(level)
        self.name = f"{self._metric.name}_gap"

    def _value(self, records: Sequence[OutcomeRecord]) -> tuple[float, Mapping[str, object]]:
        coverage, details = _required(self._metric.compute(_scores(records)))
        return float(str(details["gap"])), {**details, "coverage": coverage}


class MASEvsRandomWalkOutcome(_Adapter):
    """MAE over the no-change forecast's MAE (below 1 beats the random walk)."""

    name = core.MASEvsRandomWalk.name

    def _value(self, records: Sequence[OutcomeRecord]) -> tuple[float, Mapping[str, object]]:
        return _required(core.MASEvsRandomWalk().compute(_scores(records)))


class DieboldMarianoOutcome(_Adapter):
    """One-sided Diebold-Mariano p-value that the model beats the random walk."""

    def __init__(self, loss: str = "squared_error") -> None:
        self._metric = core.DieboldMarianoVsRandomWalk(loss)
        self.name = self._metric.name

    def _value(self, records: Sequence[OutcomeRecord]) -> tuple[float, Mapping[str, object]]:
        return _required(self._metric.compute(_scores(records), _scores(records, benchmark=True)))


class QLIKEOutcome(_Adapter):
    """QLIKE loss of the forecast variance; needs sample paths."""

    name = core.QLIKE.name

    def _value(self, records: Sequence[OutcomeRecord]) -> tuple[float, Mapping[str, object]]:
        return _required(core.QLIKE().compute(_scores(records)))
