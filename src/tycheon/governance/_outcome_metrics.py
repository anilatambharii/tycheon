"""Tycheon's outcome metrics in the shape of Keelgate's ``OutcomeMetric`` protocol.

Keelgate calls ``compute(records) -> MetricResult`` over a batch of
:class:`~keelgate.evals.OutcomeRecord`. Each adapter here hands its records to
:mod:`tycheon._metrics_worker` in a child process, which rebuilds a
:class:`~tycheon.calibration.scores.ScoreSet` and calls the matching class in
:mod:`tycheon.metrics`. No metric is computed in this module.

**Import only the standard library and Keelgate here.** ``keelgate eval run`` loads the native
``regopy`` library before it loads this module, and on Linux importing DuckDB, torch or (on
Python 3.14) matplotlib after it aborts the process. A test enforces this.

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

import json
import math
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from keelgate.evals import MetricResult, OutcomeRecord

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tycheon.calibration.scores import ScoreSet

#: ``metadata["schema"]`` of a record these metrics read.
RECORD_SCHEMA = "tycheon.forecast/1"

#: How long one metric may take in its child process.
WORKER_TIMEOUT_SECONDS = 300.0


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


def _run_worker(request: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """Ask :mod:`tycheon._metrics_worker` for one metric (fixed argv). Any failure raises."""
    done = subprocess.run(
        [sys.executable, "-P", "-m", "tycheon._metrics_worker"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=WORKER_TIMEOUT_SECONDS,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"the metrics worker exited with {done.returncode}: {done.stderr[-1000:]}"
        )
    answer = json.loads(done.stdout)
    if not answer["ok"]:
        raise ValueError(answer["error"])
    return float(answer["value"]), dict(answer["details"])


class _Adapter:
    """Shared handling of the batch; subclasses name the metric."""

    name: str
    higher_is_better = False
    #: The metric's key in ``tycheon.metrics.REGISTERED``, and its constructor argument.
    _kind: str
    _argument: float | str | None = None

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
        value, details = _run_worker(
            {
                "metric": self._kind,
                "argument": self._argument,
                "records": [
                    {
                        "predicted": dict(r.predicted),
                        "realized": dict(r.realized),
                        "metadata": dict(r.metadata),
                    }
                    for r in ours
                ],
            }
        )
        value, details = self._report(value, details)
        return MetricResult(self.name, value, len(ours), self.higher_is_better, details)

    def _report(self, value: float, details: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        return value, details


class CRPSOutcome(_Adapter):
    """Mean CRPS of the horizon-end return."""

    name = "crps"
    _kind = "crps"


class CoverageGapOutcome(_Adapter):
    """Distance between achieved and nominal coverage of the central interval."""

    _kind = "coverage"

    def __init__(self, level: float = 0.9) -> None:
        if not 0.0 < level < 1.0:
            raise ValueError("level must be strictly between 0 and 1")
        self._argument = level
        self.name = f"coverage_{round(level * 100)}_gap"

    def _report(self, value: float, details: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        return float(details["gap"]), {**details, "coverage": value}


class MASEvsRandomWalkOutcome(_Adapter):
    """MAE over the no-change forecast's MAE (below 1 beats the random walk)."""

    name = "mase_vs_random_walk"
    _kind = "mase_vs_rw"


class DieboldMarianoOutcome(_Adapter):
    """One-sided Diebold-Mariano p-value that the model beats the random walk."""

    _kind = "dm_test"

    def __init__(self, loss: str = "squared_error") -> None:
        if loss not in ("squared_error", "crps"):
            raise ValueError("loss must be 'squared_error' or 'crps'")
        self._argument = loss
        self.name = f"dm_p_{loss}"


class QLIKEOutcome(_Adapter):
    """QLIKE loss of the forecast variance; needs sample paths."""

    name = "qlike"
    _kind = "qlike"
