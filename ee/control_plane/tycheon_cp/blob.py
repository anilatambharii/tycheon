"""Blob storage for uploaded data and model artifacts.

Every key lives under ``orgs/<org_id>/``; :class:`LocalBlobStore` refuses any key that is not, and
any key that resolves outside its root. An S3-compatible store is the production target and is not
implemented here (see the documented gaps): both would implement :class:`BlobStore`.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Protocol

_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BlobError(Exception):
    """A key is malformed or outside its tenant."""


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def directory(self, prefix: str) -> Path:
        """A local directory for ``prefix`` (created if missing) for libraries that need paths."""
        ...


def org_key(org_id: str, *parts: str) -> str:
    """``orgs/<org_id>/<parts...>`` with every part validated."""
    for part in (org_id, *parts):
        if not _PART.fullmatch(part):
            raise BlobError("a storage path part is not a plain identifier")
    return "/".join(["orgs", org_id, *parts])


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if len(parts) < 3 or parts[0] != "orgs" or not all(_PART.fullmatch(p) for p in parts):
            raise BlobError("a storage key must be orgs/<org>/...")
        path = self.root.joinpath(*parts).resolve()
        if self.root not in path.parents:
            raise BlobError("a storage key resolves outside the store")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise BlobError("no such stored object")
        return path.read_bytes()

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix.rstrip("/"))
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()

    def directory(self, prefix: str) -> Path:
        path = self._path(prefix.rstrip("/"))
        path.mkdir(parents=True, exist_ok=True)
        return path
