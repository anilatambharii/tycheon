"""Run Keelgate's in-process Rego engine in a child process.

``regopy`` (Keelgate's Rego evaluator) and DuckDB (Tycheon's as-of store) corrupt each other's
heap when they share a process on Linux, in either import order. Tycheon needs both, so the policy
engine runs in a small worker process that never imports DuckDB. It is still Keelgate's
``RegoEngine`` evaluating Keelgate's ``finance_basic`` pack; only the process boundary is ours.

Anything that goes wrong (worker missing, crashed, slow, or answering garbage) is a DENY, and a
broken worker is discarded and restarted on the next call.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO

from keelgate.policy import (
    PolicyDecision,
    PolicyInput,
    hash_sources,
    load_pack_sources,
    pack_path,
)
from keelgate.policy.engine import deny

WORKER = Path(__file__).with_name("_rego_worker.py")
DEFAULT_TIMEOUT_SECONDS = 30.0


class _Worker:
    """One live child process speaking line-delimited JSON."""

    def __init__(self, query: str | None, timeout: float) -> None:
        argv = [sys.executable, str(WORKER)] + ([query] if query else [])
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv: our interpreter and script
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._timeout = timeout
        threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()
        ready = json.loads(self._read())
        if ready.get("ready") is not True:
            raise RuntimeError("policy worker did not start")

    def _pump(self, stream: IO[str] | None) -> None:
        if stream is not None:
            for line in stream:
                self._lines.put(line)
        self._lines.put(None)

    def _read(self) -> str:
        try:
            line = self._lines.get(timeout=self._timeout)
        except queue.Empty:
            raise TimeoutError("policy worker timed out") from None
        if line is None:
            raise RuntimeError("policy worker exited")
        return line

    def ask(self, document: dict[str, object]) -> str:
        stdin = self._proc.stdin
        if stdin is None:
            raise RuntimeError("policy worker has no stdin")
        stdin.write(json.dumps(document) + "\n")
        stdin.flush()
        return self._read()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._proc.kill()
            self._proc.wait(timeout=5)
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()


class OutOfProcessRegoEngine:
    """A Keelgate ``PolicyEngine`` whose Rego evaluation runs in a worker process."""

    name = "rego-subprocess"

    def __init__(self, *, query: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._query = query
        self._timeout = timeout
        self._lock = threading.Lock()
        self._worker: _Worker | None = None
        # Same hash the in-process engine reports, computed without loading any interpreter.
        self.policy_version = hash_sources(load_pack_sources(pack_path("finance_basic")))
        atexit.register(self.close)

    def close(self) -> None:
        with self._lock:
            if self._worker is not None:
                self._worker.close()
                self._worker = None

    def _decide_blocking(self, document: dict[str, object]) -> PolicyDecision:
        with self._lock:
            try:
                if self._worker is None:
                    self._worker = _Worker(self._query, self._timeout)
                return PolicyDecision.model_validate_json(self._worker.ask(document))
            except Exception as exc:  # fail closed, and never reuse a worker in an unknown state
                if self._worker is not None:
                    self._worker.close()
                    self._worker = None
                return deny(
                    f"policy evaluation failed: {type(exc).__name__}",
                    engine=self.name,
                    policy_version=self.policy_version,
                )

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        try:
            document = policy_input.to_document()
        except Exception as exc:  # NaN, non-JSON values, ...: refuse rather than guess
            return deny(
                f"policy input rejected: {type(exc).__name__}",
                engine=self.name,
                policy_version=self.policy_version,
            )
        return await asyncio.to_thread(self._decide_blocking, dict(document))
