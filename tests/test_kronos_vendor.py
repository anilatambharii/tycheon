"""The vendored upstream Kronos: byte-identical, licensed, and loaded without side effects."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types

import pytest

from tycheon.errors import ModelError, OptionalDependencyError
from tycheon.models.kronos import vendor

torch = pytest.importorskip("torch")

REPO = vendor.vendor_root()
UPSTREAM_COMMIT = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"  # pragma: allowlist secret


def test_vendored_files_match_the_upstream_manifest() -> None:
    """Fails on any edit, reformat or line-ending change to third_party/kronos."""
    manifest = vendor.verify_vendored()
    assert manifest["repository"] == "https://github.com/shiyu-coder/Kronos"
    assert (
        manifest["commit"] == "67b630e67f6a18c9e9be918d9b4337c960db1e9a"  # pragma: allowlist secret
    )  # pragma: allowlist secret
    assert set(manifest["files"]) == {"LICENSE", "model/kronos.py", "model/module.py"}


def test_the_upstream_mit_licence_and_copyright_notice_are_retained() -> None:
    """AGENTS.md: Kronos is MIT and its notice must travel with the code."""
    licence = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert "Copyright (c) 2025 ShiYu" in licence
    assert (REPO / "README.md").is_file()


def test_the_manifest_hashes_are_sha256() -> None:
    manifest = json.loads((REPO / "UPSTREAM.json").read_text(encoding="utf-8"))
    for digest in manifest["files"].values():
        assert len(digest) == 64
        int(digest, 16)


def _copy(tmp_path):
    target = tmp_path / "kronos"
    shutil.copytree(REPO, target)
    return target


def test_an_edited_vendored_file_is_detected(tmp_path) -> None:
    target = _copy(tmp_path)
    kronos = target / "model" / "kronos.py"
    kronos.write_text(kronos.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ModelError, match="was modified"):
        vendor.verify_vendored(target)


def test_crlf_line_endings_do_not_count_as_a_modification(tmp_path) -> None:
    """A Windows checkout may rewrite line endings; hashes are taken over LF-normalised bytes."""
    target = _copy(tmp_path)
    for name in ("kronos.py", "module.py"):
        path = target / "model" / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    vendor.verify_vendored(target)


def test_a_missing_vendor_tree_is_a_clear_error(monkeypatch, tmp_path) -> None:
    fake_package = types.SimpleNamespace(__file__=str(tmp_path / "src" / "tycheon" / "__init__.py"))
    monkeypatch.setattr(vendor, "tycheon", fake_package)
    with pytest.raises(ModelError, match="vendored Kronos not found"):
        vendor.vendor_root()


def test_loading_leaves_sys_path_and_sys_modules_as_it_found_them() -> None:
    """Upstream appends a relative ../ to sys.path and imports a top-level `model` package."""
    saved_cache = dict(vendor._CACHE)
    vendor._CACHE.clear()
    try:
        before_path = list(sys.path)
        before_modules = {k for k in sys.modules if k == "model" or k.startswith("model.")}
        upstream = vendor.load_upstream()
        assert sys.path == before_path, "sys.path was left modified"
        leaked = {k for k in sys.modules if k == "model" or k.startswith("model.")} - before_modules
        assert not leaked, f"top-level `model` leaked into sys.modules: {leaked}"
        assert upstream.kronos_cls.__name__ == "Kronos"
        assert upstream.tokenizer_cls.__name__ == "KronosTokenizer"
        assert callable(upstream.top_k_top_p_filtering)
        assert upstream.commit == UPSTREAM_COMMIT
    finally:
        vendor._CACHE.clear()
        vendor._CACHE.update(saved_cache)


def test_an_unrelated_top_level_model_package_is_not_clobbered(monkeypatch) -> None:
    """If the application already has its own `model` module, loading Kronos must restore it."""
    saved_cache = dict(vendor._CACHE)
    vendor._CACHE.clear()
    mine = types.ModuleType("model")
    mine.sentinel = "application model"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "model", mine)
    try:
        vendor.load_upstream()
        assert sys.modules["model"] is mine
        assert sys.modules["model"].sentinel == "application model"  # type: ignore[attr-defined]
    finally:
        vendor._CACHE.clear()
        vendor._CACHE.update(saved_cache)


def test_the_loader_caches_one_import() -> None:
    assert vendor.load_upstream() is vendor.load_upstream()


def test_missing_dependencies_become_an_actionable_error(monkeypatch) -> None:
    saved_cache = dict(vendor._CACHE)
    vendor._CACHE.clear()
    monkeypatch.setitem(sys.modules, "einops", None)  # makes `import einops` raise ImportError
    try:
        with pytest.raises(OptionalDependencyError, match="tycheon\\[kronos\\]"):
            vendor.load_upstream()
    finally:
        vendor._CACHE.clear()
        vendor._CACHE.update(saved_cache)
    assert "model" not in sys.modules or sys.modules["model"].__name__ == "model"


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )


@pytest.mark.parametrize(
    "module",
    ["tycheon.models", "tycheon.models.kronos", "tycheon.models.timesfm", "tycheon.models.chronos"],
)
def test_importing_a_model_package_does_not_import_torch(module) -> None:
    """The base install has no torch; listing the models must not require it."""
    result = _run(f"import sys, {module}; assert 'torch' not in sys.modules, 'torch was imported'")
    assert result.returncode == 0, result.stderr
