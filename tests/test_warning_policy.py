"""The suite treats warnings as errors; one known third-party warning is the only exception."""

from __future__ import annotations

import tomllib
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
XET_MESSAGE = (
    "hf_xet.download_files() is deprecated. "
    "Use XetSession().new_file_download_group().start_download_file() instead."
)


def test_warnings_are_errors_by_default() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    filters = config["tool"]["pytest"]["ini_options"]["filterwarnings"]
    assert filters[0] == "error"


def test_an_unrelated_deprecation_warning_still_fails_the_test() -> None:
    with pytest.raises(DeprecationWarning, match="something else"):
        warnings.warn("something else is deprecated", DeprecationWarning, stacklevel=1)


def test_the_hf_xet_download_deprecation_does_not_abort_model_downloads() -> None:
    """The nightly job downloads real weights; this warning used to abort every download."""
    # With the project filter this passes silently; without it, the suite's "error" filter
    # would raise DeprecationWarning here.
    warnings.warn(XET_MESSAGE, DeprecationWarning, stacklevel=1)


def test_the_ignore_is_narrow() -> None:
    """Only the exact message is ignored: a different hf_xet deprecation still fails."""
    with pytest.raises(DeprecationWarning, match=r"hf_xet\.upload_files"):
        warnings.warn("hf_xet.upload_files() is deprecated", DeprecationWarning, stacklevel=1)
