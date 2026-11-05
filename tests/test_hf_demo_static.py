"""The precomputed data behind the Hugging Face demo Space, and what the Space may contain."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
HF = Path(__file__).resolve().parents[1] / "deploy" / "huggingface"
sys.path.insert(0, str(HF))
import build_static  # noqa: E402
import deploy  # noqa: E402

DISCLAIMER = "For research and risk analytics. Not investment advice."


@pytest.fixture(scope="module")
def small() -> dict:
    return build_static.build(
        symbols=("SYN-GBM",), models=("random-walk",), forecast_horizons=(5,), risk_horizons=(5,)
    )


def test_every_grid_cell_has_a_calibrated_and_a_raw_entry(small: dict) -> None:
    for kind in ("forecast", "risk"):
        assert set(small[kind]) == {
            "SYN-GBM|random-walk|5|cal",
            "SYN-GBM|random-walk|5|raw",
        }


def test_entries_are_real_service_outputs_with_the_fields_the_page_reads(small: dict) -> None:
    f = small["forecast"]["SYN-GBM|random-walk|5|cal"]
    assert f["horizon_end"]["lower_90"] < f["horizon_end"]["median"] < f["horizon_end"]["upper_90"]
    assert f["calibration_status"] in {"calibrated", "stale", "uncalibrated"}
    raw = small["forecast"]["SYN-GBM|random-walk|5|raw"]
    assert raw["calibration_status"] == "uncalibrated"
    r = small["risk"]["SYN-GBM|random-walk|5|cal"]
    assert {m["kind"] for m in r["var_es"]} == {"VaR", "ES"}
    assert all(0 < m["loss_fraction"] < 1 for m in r["var_es"])


def test_calibrated_risk_is_never_smaller_than_raw(small: dict) -> None:
    cal = small["risk"]["SYN-GBM|random-walk|5|cal"]["var_es"]
    raw = small["risk"]["SYN-GBM|random-walk|5|raw"]["var_es"]
    for c, r in zip(cal, raw, strict=True):
        assert (c["kind"], c["level"]) == (r["kind"], r["level"])
        assert c["loss_fraction"] >= r["loss_fraction"] - 1e-9


def test_data_is_synthetic_labelled_and_serialisable(small: dict) -> None:
    assert small["disclaimer"] == DISCLAIMER
    assert all(s.startswith("SYN-") for s in small["symbols"])
    json.dumps(small)


def test_the_page_states_the_limits_and_the_disclaimer() -> None:
    html = (HF / "site" / "index.html").read_text(encoding="utf-8")
    assert DISCLAIMER in html
    assert "synthetic" in html.lower() and "precomputed" in html.lower()
    assert "<script src" not in html  # nothing is loaded from elsewhere
    readme = (HF / "README.md").read_text(encoding="utf-8")
    assert "sdk: static" in readme and "license: apache-2.0" in readme


def test_default_grids_cover_only_bundled_series_and_cheap_models() -> None:
    from tycheon.data.sample import SAMPLE_DATASETS

    assert set(build_static.SYMBOLS) <= set(SAMPLE_DATASETS)
    assert set(build_static.MODELS) == {"random-walk", "drift", "garch"}


def test_staging_writes_only_the_three_allowed_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        build_static,
        "write",
        lambda path: path.write_text("{}", encoding="utf-8"),
    )
    files = deploy.stage(tmp_path)
    assert {p.name for p in files} == deploy.ALLOWED


def test_staging_refuses_anything_else(tmp_path: Path, monkeypatch) -> None:
    def sneaky(path: Path) -> None:
        path.write_text("{}", encoding="utf-8")
        (path.parent / ".env").write_text("HF_TOKEN=x", encoding="utf-8")

    monkeypatch.setattr(build_static, "write", sneaky)
    with pytest.raises(SystemExit):
        deploy.stage(tmp_path)


def test_token_is_read_from_env_file_without_being_returned_from_elsewhere(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    assert deploy.load_dotenv_token() is None
    (tmp_path / ".env").write_text('OTHER=1\nHF_TOKEN="abc"\n', encoding="utf-8")
    assert deploy.load_dotenv_token() == "abc"
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert deploy.load_dotenv_token() == "from-env"
