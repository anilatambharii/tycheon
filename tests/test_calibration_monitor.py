"""The scheduled calibration monitor: out-of-sample coverage, Prometheus text, CLI exit code."""

from __future__ import annotations

import json

import pytest

from tycheon.calibration.monitor import evaluate_series, main, to_prometheus
from tycheon.data.sample import load_sample, sample_end
from tycheon.errors import LookaheadError
from tycheon.models.baselines import RandomWalkForecaster

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def row():
    return evaluate_series(
        RandomWalkForecaster(),
        load_sample("SYN-GBM"),
        series="SYN-GBM",
        as_of=sample_end("SYN-GBM"),
        horizon=5,
        n_origins=60,
        n_samples=50,
    )


def test_row_reports_holdout_coverage_near_nominal(row) -> None:
    assert row.status in {"calibrated", "stale", "uncalibrated"}
    assert row.n_holdout >= 10
    assert row.coverage_calibrated is not None
    assert row.gap is not None and row.gap < 0.15
    assert {n for n, _, _ in row.coverages} >= {0.5, 0.9}


def test_rows_refuse_bars_published_after_as_of() -> None:
    bars = load_sample("SYN-GBM")
    early = bars["available_at"].iloc[len(bars) // 2]
    with pytest.raises(LookaheadError):
        evaluate_series(
            RandomWalkForecaster(), bars, series="SYN-GBM", as_of=early, horizon=5, n_origins=20
        )


def test_prometheus_text_has_one_sample_per_nominal_and_escapes_labels(row) -> None:
    text = to_prometheus([row])
    assert text.endswith("\n")
    assert "# TYPE tycheon_eval_coverage gauge" in text
    assert text.count("tycheon_eval_coverage{") == len(row.coverages)
    assert 'series="SYN-GBM"' in text
    assert "tycheon_eval_calibrated{" in text
    assert "tycheon_eval_last_run_timestamp_seconds " in text
    from dataclasses import replace

    nasty = to_prometheus([replace(row, series='a"b\nc')])
    assert 'series="a\\"b c"' in nasty


def test_cli_writes_json_and_prom_and_gates_on_max_gap(tmp_path) -> None:
    args = ["--symbols", "SYN-GBM", "--origins", "60", "--samples", "30", "--out", str(tmp_path)]
    assert main(args) == 0
    payload = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert payload["disclaimer"] == "For research and risk analytics. Not investment advice."
    assert payload["rows"][0]["series"] == "SYN-GBM"
    assert (tmp_path / "calibration.prom").read_text(encoding="utf-8").startswith("# HELP")
    assert main([*args, "--max-gap", "0.0"]) == 1


def test_a_series_that_cannot_be_scored_fails_the_gate(tmp_path) -> None:
    few = ["--symbols", "SYN-GBM", "--origins", "20", "--samples", "30", "--out", str(tmp_path)]
    assert main(few) == 0  # no gate requested
    assert main([*few, "--max-gap", "0.5"]) == 1  # no evidence is not a pass


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["--models", "nope", "--symbols", "SYN-GBM"])
