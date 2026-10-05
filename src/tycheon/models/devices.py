"""Resolve a requested compute device to one that actually exists, or say why not."""

from __future__ import annotations

from typing import Any

from tycheon.errors import ModelError


def resolve_device(requested: str, torch_module: Any) -> str:
    """Return a concrete torch device string for ``requested``.

    ``"auto"`` prefers CUDA, then Apple MPS, then CPU, the same order upstream Kronos
    uses. An explicit request for an unavailable accelerator is an error rather than
    a silent fall back to CPU: a run that was supposed to be fast and quietly is not
    should fail where someone can see it.
    """
    wanted = requested.strip().lower()
    if wanted == "auto":
        if torch_module.cuda.is_available():
            return "cuda:0"
        mps = getattr(torch_module.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"
    if wanted == "cpu":
        return "cpu"
    if wanted == "cuda" or wanted.startswith("cuda:"):
        if not torch_module.cuda.is_available():
            raise ModelError("device 'cuda' was requested but CUDA is not available")
        return "cuda:0" if wanted == "cuda" else wanted
    if wanted == "mps":
        mps = getattr(torch_module.backends, "mps", None)
        if mps is None or not mps.is_available():
            raise ModelError("device 'mps' was requested but Apple MPS is not available")
        return "mps"
    raise ValueError(f"unknown device {requested!r}; use 'auto', 'cpu', 'cuda' or 'mps'")
