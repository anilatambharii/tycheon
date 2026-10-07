"""Serving a tenant's own models: ``ft:<id>`` and ``routed``.

Resolution is per tenant by construction: the registry lookup runs under row-level security for
the calling organisation, so another tenant's model id is simply "not found", and only a model
whose gate passed (``promoted``) is ever loaded. Artifacts are checked against the SHA-256 stored
at training time before any weights are read, and are loaded from ``safetensors`` (no pickle).

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tycheon.services import make_forecaster
from tycheon_cp.bridge import ModelUnavailableError
from tycheon_cp.errors import ApiProblem
from tycheon_ft import registry
from tycheon_ft.providers import forecaster_from

if TYPE_CHECKING:
    from uuid import UUID

    from tycheon.models.base import Forecaster
    from tycheon_cp.blob import BlobStore
    from tycheon_cp.db import Database
    from tycheon_ft.trainer import BaseModels

ARTIFACT_FILES = ("config.json", "model.safetensors")
MODEL_CARD = "docs/models/kronos-mini.md (fine-tuned; see the model's gate report)"


def artifact_digest(files: dict[str, bytes]) -> str:
    """One hash over the whole artifact, order-independent of upload."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return digest.hexdigest()


class PrivateModelResolver:
    """Implements the control plane's ``PrivateModels`` protocol over the registry."""

    def __init__(
        self, db: Database, blobs: BlobStore, base: BaseModels, *, cache_size: int = 4
    ) -> None:
        self.db, self.blobs, self.base = db, blobs, base
        self._cache: OrderedDict[tuple[str, str], Forecaster] = OrderedDict()
        self._cache_size = cache_size
        self._tokenizer: Any | None = None
        self._lock = asyncio.Lock()

    async def resolve(self, org_id: UUID, name: str, symbol: str | None) -> Forecaster:
        if name == "routed":
            routing = await registry.get_routing(self.db, org_id)
            name = routing.get("overrides", {}).get(symbol or "", routing["default_model"])
        if name in registry.BUILTIN_MODELS:
            return make_forecaster(name)
        model_id = registry.parse_ft(name)
        if model_id is None:
            raise ModelUnavailableError("That is not a model name.")
        return await self._load(org_id, model_id)

    async def _load(self, org_id: UUID, model_id: UUID) -> Forecaster:
        key = (str(org_id), str(model_id))
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            try:
                row = await registry.get_model(self.db, org_id, model_id, internal=True)
            except ApiProblem as exc:  # not found for this tenant: never say whose it might be
                raise ModelUnavailableError("No such model.") from exc
            if row["status"] != "promoted":
                raise ModelUnavailableError(
                    f"That model is {row['status']}; only promoted models can be used."
                )
            forecaster = await asyncio.to_thread(self._build, row)
            self._cache[key] = forecaster
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            return forecaster

    def _build(self, row: dict[str, Any]) -> Forecaster:
        from tycheon.models.kronos.vendor import load_upstream  # noqa: PLC0415

        files = {n: self.blobs.get(f"{row['artifact_key']}/{n}") for n in ARTIFACT_FILES}
        if artifact_digest(files) != row["artifact_sha256"]:
            raise ModelUnavailableError("The stored model failed its integrity check.")
        if self._tokenizer is None:
            self._tokenizer = self.base.load()[0]
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in files.items():
                (Path(tmp) / name).write_bytes(data)
            model = load_upstream().kronos_cls.from_pretrained(tmp)
        return forecaster_from(
            self._tokenizer,
            model,
            variant=self.base.variant,
            max_context=self.base.max_context,
            name=f"ft:{row['id']}",
            card=MODEL_CARD,
        )
