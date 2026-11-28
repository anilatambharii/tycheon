"""The one place Tycheon imports DuckDB, because the import order matters on Linux.

Keelgate's policy engine uses the native ``regopy`` library. On Linux, importing DuckDB *after*
``regopy`` is loaded aborts the process (``double free or corruption``), even if neither is
used. The other order loads cleanly, so DuckDB is normally imported as soon as this module is.

When ``regopy`` is already loaded (``keelgate eval run`` loads it before it loads Tycheon's
outcome metrics), the import is skipped. Code that never opens a store, such as those metrics,
then works. Opening a store in that state raises on Linux, where the import would otherwise kill
the process.
"""

from __future__ import annotations

import sys
from typing import Any

if "regopy" not in sys.modules:
    import duckdb  # noqa: F401


def connect(database: str) -> Any:
    """``duckdb.connect(database)``, refusing the import that would abort the process."""
    if sys.platform.startswith("linux") and "duckdb" not in sys.modules and "regopy" in sys.modules:
        raise RuntimeError(
            "DuckDB cannot be imported after Keelgate's regopy library on Linux: the process "
            "would abort. Import tycheon (or duckdb) before keelgate.policy or keelgate.evals."
        )
    import duckdb  # noqa: PLC0415

    return duckdb.connect(database)
