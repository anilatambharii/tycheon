"""Load the vendored upstream Kronos without letting it touch the process.

``third_party/kronos`` holds two upstream files, byte-identical (ADR 0002). They were
written to be run from a checkout and do two things a library must not tolerate:

* ``sys.path.append("../")`` appends a *relative* directory to ``sys.path``. Relative
  to wherever the process happens to be, that makes imports resolvable from the
  parent of the working directory, which is both a correctness hazard and a way to
  have unrelated code imported by accident.
* ``from model.module import *`` needs a top-level package called ``model``, a name
  far too generic to claim globally.

So the loader executes the two files by path under unique module names, provides the
one ``model.module`` import they ask for through a temporary stand-in, and then
restores ``sys.path`` and ``sys.modules`` exactly as it found them. A SHA-256 check
against ``UPSTREAM.json`` runs first, so edited vendored code never loads silently.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import tycheon
from tycheon.errors import ModelError, OptionalDependencyError

if TYPE_CHECKING:
    from collections.abc import Callable

_LOCK = threading.Lock()
_CACHE: dict[str, Upstream] = {}

_MODULE_NAME = "tycheon._vendor.kronos_module"
_KRONOS_NAME = "tycheon._vendor.kronos_model"


class Upstream(NamedTuple):
    """The pieces of upstream Kronos that Tycheon uses."""

    kronos_cls: Any
    tokenizer_cls: Any
    top_k_top_p_filtering: Callable[..., Any]
    root: Path
    commit: str


def vendor_root() -> Path:
    """Where the vendored files live: inside an installed wheel, else the repo checkout."""
    package = Path(tycheon.__file__).resolve().parent
    candidates = (package / "_vendor" / "kronos", package.parents[1] / "third_party" / "kronos")
    for candidate in candidates:
        if (candidate / "model" / "kronos.py").is_file():
            return candidate
    raise ModelError(
        "vendored Kronos not found; looked in "
        + ", ".join(str(c) for c in candidates)
        + ". Reinstall Tycheon or run from a full checkout."
    )


def _digest(path: Path) -> str:
    # LF-normalised so a CRLF checkout on Windows hashes the same as upstream.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify_vendored(root: Path | None = None) -> dict[str, Any]:
    """Raise unless every vendored file matches ``UPSTREAM.json``; return the manifest."""
    base = root or vendor_root()
    manifest: dict[str, Any] = json.loads((base / "UPSTREAM.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        actual = _digest(base / relative)
        if actual != expected:
            raise ModelError(
                f"vendored Kronos file {relative} was modified (sha256 {actual[:12]}... does "
                f"not match upstream {expected[:12]}...). Do not edit third_party/kronos."
            )
    return manifest


def _exec(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ModelError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def load_upstream() -> Upstream:
    """Import upstream Kronos (once) and return the classes Tycheon needs."""
    with _LOCK:
        cached = _CACHE.get("upstream")
        if cached is not None:
            return cached

        root = vendor_root()
        manifest = verify_vendored(root)

        saved_path = list(sys.path)
        saved_modules = {k: sys.modules.get(k) for k in ("model", "model.module")}
        try:
            module = _exec(_MODULE_NAME, root / "model" / "module.py")
            stand_in = types.ModuleType("model")
            stand_in.__path__ = []
            sys.modules["model"] = stand_in
            sys.modules["model.module"] = module
            kronos = _exec(_KRONOS_NAME, root / "model" / "kronos.py")
        except ImportError as exc:
            raise OptionalDependencyError(
                f"Kronos needs the 'kronos' extra ({exc}); install it with "
                "`pip install 'tycheon[kronos]'`"
            ) from exc
        finally:
            sys.path[:] = saved_path
            for key, previous in saved_modules.items():
                if previous is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = previous

        _CACHE["upstream"] = loaded = Upstream(
            kronos_cls=kronos.Kronos,
            tokenizer_cls=kronos.KronosTokenizer,
            top_k_top_p_filtering=kronos.top_k_top_p_filtering,
            root=root,
            commit=str(manifest["commit"]),
        )
        return loaded
