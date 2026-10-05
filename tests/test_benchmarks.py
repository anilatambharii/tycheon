"""The leaderboard runner has one job in T0: refuse a config we could not publish."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.run import ConfigError, describe, load_config, main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMALL = PROJECT_ROOT / "benchmarks" / "configs" / "small.yaml"


def _write(tmp_path: Path, config: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.fixture
def valid_config() -> dict[str, Any]:
    data: Any = yaml.safe_load(SMALL.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_small_config_is_valid() -> None:
    config = load_config(SMALL)
    assert config["name"] == "small"


def test_small_config_is_described_without_running_anything() -> None:
    text = describe(load_config(SMALL))
    assert "random-walk" in text
    assert "embargo" in text


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no such config"):
        load_config(tmp_path / "nope.yaml")


def test_config_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


def test_missing_keys_are_reported(tmp_path: Path, valid_config: dict[str, Any]) -> None:
    del valid_config["horizons"]
    with pytest.raises(ConfigError, match="horizons"):
        load_config(_write(tmp_path, valid_config))


def test_random_walk_baseline_is_mandatory(tmp_path: Path, valid_config: dict[str, Any]) -> None:
    """Every published result is read against doing nothing."""
    valid_config["baselines"] = ["drift"]
    with pytest.raises(ConfigError, match="random-walk"):
        load_config(_write(tmp_path, valid_config))


def test_models_and_baselines_must_exist_in_the_registry(
    tmp_path: Path, valid_config: dict[str, Any]
) -> None:
    """A config naming a forecaster that does not exist could never be reproduced."""
    valid_config["models"] = ["kronos-large"]  # not an open model
    with pytest.raises(ConfigError, match="unknown models"):
        load_config(_write(tmp_path, valid_config))
    valid_config["models"] = ["kronos-small"]
    valid_config["baselines"] = ["random-walk", "buy-and-hold"]
    with pytest.raises(ConfigError, match="unknown baselines"):
        load_config(_write(tmp_path, valid_config))


def test_embargo_is_mandatory(tmp_path: Path, valid_config: dict[str, Any]) -> None:
    """Without an embargo the evaluation leaks across fold boundaries."""
    del valid_config["walk_forward"]["embargo"]
    with pytest.raises(ConfigError, match="embargo"):
        load_config(_write(tmp_path, valid_config))


def test_cli_validates_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(SMALL)]) == 0
    assert "config is valid" in capsys.readouterr().out


def test_cli_reports_a_bad_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "nope.yaml")]) == 2
    assert "config error" in capsys.readouterr().err


# ------------------------------------------------------------- stricter validation
FULL = PROJECT_ROOT / "benchmarks" / "configs" / "full.yaml"


@pytest.mark.parametrize("path", [SMALL, FULL], ids=["small", "full"])
def test_every_shipped_config_validates(path: Path) -> None:
    assert load_config(path)["name"] == path.stem


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"horizons": []}, "horizons"),
        ({"horizons": [0]}, "horizons"),
        ({"dataset": "no-such-dataset"}, "unknown dataset"),
        ({"universe": ["AAPL"]}, "not in dataset"),
        ({"universe": []}, "universe"),
        ({"universe_kind": "whatever"}, "universe_kind"),
    ],
)
def test_bad_values_are_rejected(
    tmp_path: Path, valid_config: dict[str, Any], change: dict[str, Any], match: str
) -> None:
    valid_config.update(change)
    with pytest.raises(ConfigError, match=match):
        load_config(_write(tmp_path, valid_config))


def test_overlapping_origins_are_refused(tmp_path: Path, valid_config: dict[str, Any]) -> None:
    valid_config["walk_forward"]["stride"] = 1
    with pytest.raises(ConfigError, match="stride"):
        load_config(_write(tmp_path, valid_config))


def test_the_cli_can_describe_without_running(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(FULL)]) == 0
    assert "Add --execute" in capsys.readouterr().out


# -------------------------------------------------------------------- model registry
def test_every_known_model_has_a_matching_config_file() -> None:
    from benchmarks.models import load_model_spec
    from benchmarks.run import BASELINES, KNOWN_MODELS

    for model_id in sorted(KNOWN_MODELS | BASELINES):
        spec = load_model_spec(model_id)
        assert spec.id == model_id and spec.kind in ("baseline", "foundation", "tycheon")


def test_a_missing_extra_is_reported_with_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks import models

    spec = models.load_model_spec("timesfm-2.5-200m")
    monkeypatch.setattr(models.importlib.util, "find_spec", lambda name: None)
    reason = models.unavailable_reason(spec)
    assert reason is not None and "tycheon[timesfm]" in reason
    assert models.unavailable_reason(models.load_model_spec("random-walk")) is None


def test_a_tycheon_model_is_unavailable_if_a_member_is(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks import models

    ensemble = models.load_model_spec("tycheon-ensemble")
    members = {m: models.load_model_spec(m) for m in ensemble.config["members"]}
    members["drift"] = models.ModelSpec(
        "drift", "baseline", {**members["drift"].config, "extra": "kronos"}
    )
    monkeypatch.setattr(models.importlib.util, "find_spec", lambda name: None)
    reason = models.unavailable_reason(ensemble, members)
    assert reason is not None and "ensemble member drift" in reason


def test_a_model_may_only_override_sampling_options() -> None:
    from benchmarks import models
    from tycheon.backtest import WalkForwardConfig

    spec = models.ModelSpec("x", "baseline", {"id": "x", "walk_forward": {"folds": 9}})
    with pytest.raises(models.ModelConfigError, match="may only override"):
        models.effective_walk_forward(spec, WalkForwardConfig(horizon=5))
    ok = models.ModelSpec("x", "baseline", {"id": "x", "walk_forward": {"n_samples": 7}})
    assert models.effective_walk_forward(ok, WalkForwardConfig(horizon=5)).n_samples == 7


# ----------------------------------------------------------------------- datasets
def test_the_manifest_hash_ignores_line_endings(tmp_path: Path) -> None:
    from benchmarks.datasets import MANIFEST_PATH, manifest_sha256

    crlf = tmp_path / "m.yaml"
    crlf.write_bytes(MANIFEST_PATH.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert manifest_sha256(crlf) == manifest_sha256(MANIFEST_PATH)


def test_licensed_data_needs_a_data_dir_and_is_never_bundled() -> None:
    from benchmarks.datasets import DatasetError, get_dataset, load_series

    licensed = get_dataset("user-csv-daily")
    assert licensed.redistributable is False and licensed.synthetic is False
    with pytest.raises(DatasetError, match="--data-dir"):
        load_series(licensed, "ANY", None)
    synthetic = get_dataset("sample-synthetic-daily")
    assert synthetic.redistributable and len(load_series(synthetic, "SYN-GBM", None)) == 1500
    with pytest.raises(DatasetError, match="not a bundled"):
        load_series(synthetic, "AAPL", None)


def test_licensed_csv_data_is_read_through_the_point_in_time_provider(tmp_path: Path) -> None:
    import numpy as np
    import pandas as pd

    from benchmarks.datasets import get_dataset, load_series

    index = pd.bdate_range("2020-01-01", periods=30)
    close = 100 + np.arange(30.0)
    frame = pd.DataFrame(
        {"timestamp": index, "open": close, "high": close + 1, "low": close - 1, "close": close,
         "volume": 1e6}
    )  # fmt: skip
    frame.to_csv(tmp_path / "MINE.csv", index=False)
    bars = load_series(get_dataset("user-csv-daily"), "MINE", tmp_path)
    assert len(bars) == 30 and "available_at" in bars.columns


# -------------------------------------------------------------- execution, end to end
@pytest.fixture(scope="module")
def tiny_result() -> dict[str, Any]:
    from benchmarks.execute import execute

    config = load_config(SMALL)
    config.update(
        {
            "name": "tiny",
            "universe": ["SYN-GBM"],
            "baselines": ["random-walk", "drift"],
            "models": ["tycheon-calibrated", "timesfm-2.5-200m"],
        }
    )
    config["walk_forward"].update({"folds": 2, "test_window": 20, "train_window": 500})
    return execute(config, say=lambda _msg: None)


def test_a_run_has_the_random_walk_row_and_diebold_mariano_for_every_model(tiny_result) -> None:
    models = tiny_result["results"][0]["models"]
    assert models["random-walk"]["pooled"]["is_benchmark"] is True
    for name in ("drift", "tycheon-calibrated"):
        dm = models[name]["pooled"]["diebold_mariano_vs_random_walk"]
        assert 0 <= dm["squared_error"]["p_two_sided"] <= 1 and "crps" in dm
        assert models[name]["leakage_controls"]["guard_checks"] == 8  # one per origin
        assert models[name]["leakage_controls"]["refits"] == 2  # one per fold


def test_the_result_document_carries_provenance(tiny_result) -> None:
    assert tiny_result["schema_version"] == 1 and tiny_result["tycheon_version"]
    assert len(tiny_result["config_sha256"]) == 64
    assert tiny_result["dataset"]["synthetic"] is True
    assert tiny_result["dataset"]["manifest_sha256"]
    assert "Not investment advice" in tiny_result["disclaimer"]
    assert "hostname" not in json.dumps(tiny_result).lower()


def test_a_model_that_cannot_run_is_reported_not_dropped(tiny_result) -> None:
    import importlib.util

    status = tiny_result["model_status"]["timesfm-2.5-200m"]
    if importlib.util.find_spec("timesfm") is None:
        assert status["status"] == "skipped" and "tycheon[timesfm]" in status["reason"]
        assert "timesfm-2.5-200m" not in tiny_result["results"][0]["models"]


def test_the_leaderboard_is_rendered_from_the_json(tiny_result, tmp_path: Path) -> None:
    from benchmarks.render import render_leaderboard
    from benchmarks.run import write_result

    path = write_result(tiny_result, tmp_path)
    assert path == tmp_path / "tiny" / f"{tiny_result['tycheon_version']}.json"
    page = render_leaderboard(tmp_path, tmp_path / "docs" / "leaderboard.md").read_text(
        encoding="utf-8"
    )
    for needle in (
        "random-walk",
        "(baseline)",
        "DM p, sq. error",
        "Where the baselines win",
        "Not investment advice",
        "Synthetic data",
    ):
        assert needle in page
    assert "<script" not in page.lower()


def test_rendering_escapes_text_from_the_results(tiny_result, tmp_path: Path) -> None:
    import copy

    from benchmarks.render import render_leaderboard
    from benchmarks.run import write_result

    evil = copy.deepcopy(tiny_result)
    evil["config"]["description"] = "<script>alert(1)</script> | pipe"
    write_result(evil, tmp_path)
    page = render_leaderboard(tmp_path, tmp_path / "out.md").read_text(encoding="utf-8")
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_local_results_are_not_published(tiny_result, tmp_path: Path) -> None:
    from benchmarks.render import latest_results
    from benchmarks.run import write_result

    write_result(tiny_result, tmp_path / "local")
    assert latest_results(tmp_path) == []


def test_a_lookahead_error_is_never_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks import execute as ex
    from tycheon.errors import LookaheadError

    def leak(*args: Any, **kwargs: Any) -> None:
        raise LookaheadError("peeked")

    monkeypatch.setattr(ex, "walk_forward", leak)
    config = load_config(SMALL)
    config.update({"universe": ["SYN-GBM"], "models": [], "baselines": ["random-walk"]})
    with pytest.raises(LookaheadError, match="peeked"):
        ex.execute(config, say=lambda _msg: None)


def test_a_model_that_fails_is_recorded_as_failed_and_the_run_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks import execute as ex
    from benchmarks.models import ModelSpec
    from tycheon.errors import ModelError

    real = ex.walk_forward

    def flaky(factory: Any, bars: Any, config: Any) -> Any:
        if config.n_samples == 7:  # only the model below uses this sample count
            raise ModelError("weights unavailable")
        return real(factory, bars, config)

    monkeypatch.setattr(ex, "walk_forward", flaky)
    config = load_config(SMALL)
    config["walk_forward"].update({"folds": 1, "test_window": 10, "train_window": 500})
    config.update({"universe": ["SYN-GBM"], "models": [], "baselines": ["random-walk", "drift"]})
    specs = ex.load_model_spec  # drift gets an override that triggers the failure
    monkeypatch.setattr(
        ex,
        "load_model_spec",
        lambda mid: (
            ModelSpec(
                "drift", "baseline", {**specs("drift").config, "walk_forward": {"n_samples": 7}}
            )
            if mid == "drift"
            else specs(mid)
        ),
    )
    result = ex.execute(config, say=lambda _msg: None)
    assert result["model_status"]["drift"]["status"] == "failed"
    assert "weights unavailable" in result["model_status"]["drift"]["reason"]
    assert result["model_status"]["random-walk"]["status"] == "ran"
