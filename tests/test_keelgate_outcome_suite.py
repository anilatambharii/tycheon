"""Tycheon's metrics inside Keelgate's outcome suite.

The suite tests run the real ``keelgate eval run`` command in a child process. That matters:
the command loads Keelgate's ``regopy`` library *before* it loads plugins, an order in which
importing DuckDB aborts the process on Linux. A test calling the suite inside pytest, where
DuckDB is already loaded, would pass while the command crashed.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from keelgate.evals import OutcomeMetric, OutcomeRecord, discover_metrics, sample_records
from tests.test_backtest_metrics import make_scores

from tycheon import _duckdb
from tycheon import metrics as core
from tycheon.governance._outcome_metrics import (
    CoverageGapOutcome,
    CRPSOutcome,
    DieboldMarianoOutcome,
    MASEvsRandomWalkOutcome,
    QLIKEOutcome,
    records_from_scores,
)

ENTRY_POINTS = {"crps", "coverage", "mase_vs_rw", "dm_test", "qlike"}
METRIC_NAMES = {"crps", "coverage_90_gap", "mase_vs_random_walk", "dm_p_squared_error", "qlike"}
ADAPTERS = [CRPSOutcome, CoverageGapOutcome, MASEvsRandomWalkOutcome]
ADAPTERS += [DieboldMarianoOutcome, QLIKEOutcome]


@pytest.fixture(scope="module")
def pair():
    rng = np.random.default_rng(21)
    n = 120
    signal = rng.normal(0, 0.02, n)
    real = signal + rng.normal(0, 0.015, n)
    rw = make_scores(np.zeros(n), real, model_id="random-walk", samples=True, seed=1)
    model = make_scores(signal, real, model_id="good", samples=True)
    return model, rw


def expected_values(model, rw) -> dict[str, float]:
    """The same five numbers straight from ``tycheon.metrics``, with no Keelgate involved."""
    coverage = core.IntervalCoverage(0.9).compute(model)
    values = {
        "crps": core.CRPS().compute(model).value,
        "coverage_90_gap": coverage.details["gap"],
        "mase_vs_random_walk": core.MASEvsRandomWalk().compute(model).value,
        "dm_p_squared_error": core.DieboldMarianoVsRandomWalk().compute(model, rw).value,
        "qlike": core.QLIKE().compute(model).value,
    }
    return {name: float(value) for name, value in values.items()}  # type: ignore[arg-type]


def run_outcome_suite(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess, Any]:
    command = shutil.which("keelgate", path=str(Path(sys.executable).parent))
    assert command, "the keelgate command is not installed next to this interpreter"
    out = tmp_path / "report"
    done = subprocess.run(  # noqa: S603 - fixed argv
        [
            command,
            "eval",
            "run",
            "--suite",
            "outcome",
            "--out",
            str(out),
            "--format",
            "json",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=tmp_path,
        timeout=180,
        check=False,
    )
    report = out / "report.json"
    assert report.is_file(), f"exit {done.returncode}: {done.stdout[-2000:]}{done.stderr[-2000:]}"
    return done, json.loads(report.read_text(encoding="utf-8"))


def tycheon_metrics(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in report["metrics"] if m["source"] == "tycheon"}


def test_keelgate_discovers_all_five_entry_points() -> None:
    ours = {m.entry_point: m for m in discover_metrics() if m.source == "tycheon"}
    assert set(ours) == ENTRY_POINTS
    for loaded in ours.values():
        assert loaded.error == "" and isinstance(loaded.metric, OutcomeMetric)
    assert {m.metric.name for m in ours.values()} == METRIC_NAMES  # type: ignore[union-attr]


def test_keelgates_outcome_suite_computes_all_five_metrics(pair, tmp_path) -> None:
    model, rw = pair
    records = tmp_path / "records.jsonl"
    records.write_text(
        "".join(json.dumps(asdict(r)) + "\n" for r in records_from_scores(model, rw)),
        encoding="utf-8",
    )
    done, report = run_outcome_suite(tmp_path, "--outcomes", str(records))
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    got = tycheon_metrics(report)
    assert set(got) == METRIC_NAMES
    for name, value in expected_values(model, rw).items():
        assert math.isfinite(got[name]["value"])
        assert got[name]["value"] == pytest.approx(value), name
        assert got[name]["n"] == model.n and got[name]["higher_is_better"] is False


def test_keelgates_own_sample_records_give_no_data_not_a_number(tmp_path) -> None:
    """With Tycheon installed, a default ``keelgate eval run`` must still work."""
    done, report = run_outcome_suite(tmp_path)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    got = tycheon_metrics(report)
    assert set(got) == METRIC_NAMES
    for metric in got.values():
        assert metric["n"] == 0 and metric["value"] == math.inf


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_an_adapter_matches_the_metric_whatever_the_record_order(pair, adapter) -> None:
    model, rw = pair
    records = records_from_scores(model, rw)
    shuffled = [records[i] for i in np.random.default_rng(3).permutation(len(records))]
    result = adapter().compute(shuffled + sample_records())  # other records are ignored
    assert result.value == pytest.approx(expected_values(model, rw)[result.name])
    assert result.n == model.n


def test_coverage_reports_the_gap_so_that_lower_really_is_better(pair) -> None:
    model, rw = pair
    result = CoverageGapOutcome().compute(records_from_scores(model, rw))
    assert result.value == pytest.approx(abs(result.details["coverage"] - 0.9))


def test_what_cannot_be_computed_raises(pair) -> None:
    model, _ = pair
    without_benchmark = records_from_scores(model)
    with pytest.raises(ValueError, match="missing 'benchmark'"):
        DieboldMarianoOutcome().compute(without_benchmark)
    no_paths = records_from_scores(replace(model, samples=None, n_samples=0))
    with pytest.raises(ValueError, match="qlike cannot be computed"):
        QLIKEOutcome().compute(no_paths)
    with pytest.raises(ValueError, match="either every record has sample paths"):
        CRPSOutcome().compute(without_benchmark[:5] + no_paths[5:])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"metadata": {"model_id": "another"}}, "share model_id"),
        ({"metadata": {"stride": 1}}, "share model_id"),
        ({"metadata": {"origin_time": "2020-01-01T00:00:00"}}, None),
        ({"realized": {"log_returns": [float("nan"), 0.0]}}, "must be finite"),
        ({"realized": {}}, "missing 'log_returns'"),
    ],
)
def test_a_malformed_tycheon_record_is_an_error_not_skipped(pair, change, message) -> None:
    model, _ = pair
    records = records_from_scores(model)
    first = records[0]
    parts = {"predicted": first.predicted, "realized": first.realized, "metadata": first.metadata}
    for key, value in change.items():
        parts[key] = {**parts[key], **value} if value else {}
    records[0] = OutcomeRecord(record_id="bad", **parts)
    with pytest.raises((ValueError, TypeError), match=message):
        CRPSOutcome().compute(records)


def test_records_need_a_benchmark_from_the_same_origins(pair) -> None:
    model, rw = pair
    with pytest.raises(ValueError, match="share origins"):
        records_from_scores(model, rw.take(slice(1, None)))


def test_opening_a_store_after_regopy_fails_loudly_instead_of_aborting(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "regopy", sys.modules.get("regopy") or sys)
    monkeypatch.delitem(sys.modules, "duckdb")
    with pytest.raises(RuntimeError, match="would abort"):
        _duckdb.connect(":memory:")
