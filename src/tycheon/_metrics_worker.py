"""Compute one outcome metric in a clean process, for the Keelgate adapters.

``keelgate eval run`` loads Keelgate's native ``regopy`` library before it loads plugins. On
Linux, importing some native libraries after ``regopy`` aborts the process: DuckDB and torch on
Python 3.12 and 3.14, and matplotlib (which ``tycheon.metrics`` reaches through ``arch``) on
3.14. The adapters in ``tycheon.governance`` therefore import none of Tycheon's analytics. They
run this module as ``python -m tycheon._metrics_worker``: one JSON request on stdin, one JSON
answer on stdout.

It rebuilds a :class:`~tycheon.calibration.scores.ScoreSet` from the records and calls the
matching class in :mod:`tycheon.metrics`. No metric is computed here. It imports no Keelgate
code.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import numpy as np
import pandas as pd

from tycheon import metrics as core
from tycheon.calibration.scores import ScoreSet


def build_scores(records: list[dict[str, Any]], *, benchmark: bool = False) -> ScoreSet:
    """Rebuild the model's (or the benchmark's) ``ScoreSet`` from Tycheon records."""
    try:
        origins = pd.DatetimeIndex([pd.Timestamp(r["metadata"]["origin_time"]) for r in records])
        if origins.tz is None:
            raise ValueError("origin_time needs a timezone")
        shared = {
            (
                str(r["metadata"]["model_id"]),
                int(r["metadata"]["stride"]),
                tuple(r["predicted"]["levels"]),
            )
            for r in records
        }
        if len(shared) != 1:
            raise ValueError("records in one batch must share model_id, stride and levels")
        ((model_id, stride, levels),) = shared
        forecasts = [r["predicted"]["benchmark"] if benchmark else r["predicted"] for r in records]
        quantiles = np.asarray([f["quantiles"] for f in forecasts], dtype=np.float64)
        realized = np.asarray([r["realized"]["log_returns"] for r in records], dtype=np.float64)
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


def compute(request: dict[str, Any]) -> dict[str, Any]:
    """``{"metric", "argument", "records"}`` to ``{"value", "details"}``.

    ``metric`` is a key of :data:`tycheon.metrics.REGISTERED`; ``argument`` is the coverage
    level or the Diebold-Mariano loss, for the metrics that take one.
    """
    kind, argument = request["metric"], request.get("argument")
    metric_class: Any = core.REGISTERED[kind]
    metric = metric_class() if argument is None else metric_class(argument)
    records = request["records"]
    model = build_scores(records)
    benchmark = build_scores(records, benchmark=True) if kind == "dm_test" else None
    result = metric.compute(model, benchmark)
    if result.value is None:
        raise ValueError(f"{result.name} cannot be computed on these records: {result.details}")
    return {"value": result.value, "details": result.details}


def main() -> None:
    try:
        answer = {"ok": True, **compute(json.load(sys.stdin))}
    except (ValueError, TypeError) as exc:  # bad input: say what is wrong with it
        answer = {"ok": False, "error": str(exc)}
    sys.stdout.write(json.dumps(answer))


if __name__ == "__main__":
    main()
