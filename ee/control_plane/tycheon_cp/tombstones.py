"""A durable, append-only record of erasures that lives outside the database.

The database keeps a receipt for each erased organisation, but a restore from an older backup
brings back both the organisation and the absence of its receipt. This log is the second copy:
one small JSON file per erasure, written *before* the rows are deleted, never rewritten, and
replayed by ``python -m tycheon_cp.cli reapply-erasures`` after any restore.

In production point ``TYCHEON_CP_ERASURE_LOG_DIR`` at a volume with write-once retention (an
object-lock bucket mounted through a CSI driver, for example) that the database restore does not
touch. A record holds opaque ids and a keyed hash only: no name, email or slug.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[0-9a-f-]{36}$")


@dataclass(frozen=True)
class Tombstone:
    receipt_id: str
    org_id: str
    slug_hmac: str
    requested_by: str | None
    actor_role: str
    requested_at: str
    erased_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tombstone:
        for key in ("receipt_id", "org_id"):
            if not _ID.fullmatch(str(data.get(key, ""))):
                raise ValueError(f"tombstone has a bad {key}")
        return cls(
            receipt_id=data["receipt_id"],
            org_id=data["org_id"],
            slug_hmac=str(data["slug_hmac"]),
            requested_by=data.get("requested_by"),
            actor_role=str(data["actor_role"]),
            requested_at=str(data["requested_at"]),
            erased_at=str(data["erased_at"]),
        )

    def requested_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.requested_at)

    def erased_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.erased_at)


class Tombstones:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, record: Tombstone) -> Path:
        """Write atomically and refuse to overwrite: a tombstone is never changed."""
        path = self.directory / f"{record.receipt_id}.json"
        if path.exists():
            return path
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(record), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
        return path

    def all(self) -> list[Tombstone]:
        out = []
        for path in sorted(self.directory.glob("*.json")):
            out.append(Tombstone.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return out
