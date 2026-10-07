"""Worker process for :class:`~tycheon.governance._rego_process.OutOfProcessRegoEngine`.

Run as a plain script (``python _rego_worker.py``), never imported, so that it loads Keelgate and
its native ``regopy`` library and nothing else: in particular not DuckDB, which cannot share a
process with ``regopy`` on Linux (see ``docs/governance.md``). One JSON policy input document per
stdin line in, one :class:`~keelgate.policy.PolicyDecision` JSON per stdout line out.
"""

from __future__ import annotations

import asyncio
import json
import sys


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else None
    from keelgate.policy import RegoEngine  # noqa: PLC0415 - native regopy loads here only

    engine = RegoEngine(query=query) if query else RegoEngine()
    out = sys.stdout
    out.write(json.dumps({"ready": True, "policy_version": engine.policy_version}) + "\n")
    out.flush()
    loop = asyncio.new_event_loop()
    for line in sys.stdin:
        decision = loop.run_until_complete(engine.decide_document(json.loads(line)))
        out.write(decision.model_dump_json() + "\n")
        out.flush()


if __name__ == "__main__":
    main()
