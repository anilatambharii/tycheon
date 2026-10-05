"""The leaderboard runner has one job in T0: refuse a config we could not publish."""

from __future__ import annotations

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


def test_cli_refuses_to_execute_before_the_engine_exists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Better a loud refusal than a number nobody measured."""
    assert main(["--config", str(SMALL), "--execute"]) == 3
    assert "refusing to execute" in capsys.readouterr().err
