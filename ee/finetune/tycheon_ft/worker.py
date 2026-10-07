"""The fine-tune worker: claims queued jobs, trains, judges, registers, and meters.

Run it where the GPUs are::

    python -m tycheon_ft.worker --pool shared --device auto

A worker serves one *pool* (``shared``, or ``dedicated-<org>`` for an Enterprise customer's own
GPUs). It claims a job across tenants through one SECURITY DEFINER function, then works strictly
inside that job's tenant: that tenant's data directory, that tenant's registry rows.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pandas as pd

from tycheon.data.providers.file import FileProvider
from tycheon_cp.blob import org_key
from tycheon_cp.datasources import source_directory
from tycheon_ft import registry
from tycheon_ft.dataset import (
    DatasetError,
    FineTuneConfig,
    build_windows,
    validate_bars,
)
from tycheon_ft.gate import run_gate
from tycheon_ft.loader import ARTIFACT_FILES, artifact_digest
from tycheon_ft.providers import PinnedKronos, forecaster_from
from tycheon_ft.trainer import TrainingCancelledError, fine_tune

if TYPE_CHECKING:
    from tycheon_cp.blob import BlobStore
    from tycheon_cp.db import Database
    from tycheon_cp.metering import Meter
    from tycheon_ft.trainer import BaseModels

log = logging.getLogger("tycheon_ft.worker")
CANCEL_POLL_SECONDS = 2.0
MAX_ERROR_CHARS = 300


@dataclass
class WorkerContext:
    db: Database
    blobs: BlobStore
    meter: Meter
    base: BaseModels
    device: str = "auto"
    worker_id: str = ""
    min_improvement: float = 0.02

    def __post_init__(self) -> None:
        self.worker_id = self.worker_id or f"worker-{uuid.uuid4().hex[:8]}"


async def claim(ctx: WorkerContext, pool: str) -> tuple[UUID, UUID] | None:
    async with ctx.db.anonymous() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM cp_claim_finetune_job($1, $2)", pool, ctx.worker_id
        )
    return (row["org_id"], row["job_id"]) if row else None


def _load_series(
    ctx: WorkerContext, org_id: UUID, source_id: UUID, symbols: list[str], as_of: datetime
) -> dict[str, pd.DataFrame]:
    """The tenant's bars as known at ``as_of``, read from the tenant's own directory only."""
    provider = FileProvider(source_directory(ctx.blobs, str(org_id), str(source_id)))
    stamp = pd.Timestamp(as_of)
    series: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        bars = provider.fetch_bars(symbol, start=None, end=None, as_of=as_of, frequency="1D")
        series[symbol] = validate_bars(symbol, bars, stamp)
    return series


async def _watch_cancel(
    ctx: WorkerContext, org_id: UUID, job_id: UUID, flag: threading.Event
) -> None:
    while not flag.is_set():
        if await registry.job_status(ctx.db, org_id, job_id) == "cancelled":
            flag.set()
            return
        await asyncio.sleep(CANCEL_POLL_SECONDS)


async def _progress(ctx: WorkerContext, org_id: UUID, job_id: UUID, data: dict[str, Any]) -> None:
    async with ctx.db.tenant(org_id) as conn:
        await conn.execute(
            "UPDATE finetune_jobs SET progress = $2::jsonb WHERE id = $1", job_id, json.dumps(data)
        )


def _save(model: Any) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        model.save_pretrained(tmp)
        return {name: (Path(tmp) / name).read_bytes() for name in ARTIFACT_FILES}


async def run_job(ctx: WorkerContext, org_id: UUID, job_id: UUID) -> str:
    """Run one claimed job to a terminal state. Returns that state."""
    started = time.perf_counter()
    cancelled = threading.Event()
    watcher = asyncio.create_task(_watch_cancel(ctx, org_id, job_id, cancelled))
    status, error, model_id = "failed", None, None
    try:
        job = await registry.get_job(ctx.db, org_id, job_id)
        config = FineTuneConfig.model_validate(job["params"])
        as_of = datetime.now(UTC)
        series = await asyncio.to_thread(
            _load_series, ctx, org_id, UUID(job["data_source_id"]), config.symbols, as_of
        )
        train, val, _, report = build_windows(series, config)
        await _progress(ctx, org_id, job_id, {"phase": "training", "windows": len(train)})
        loop = asyncio.get_running_loop()

        def on_progress(info: dict[str, Any]) -> None:
            asyncio.run_coroutine_threadsafe(
                _progress(ctx, org_id, job_id, {"phase": "training", **info}), loop
            )

        trained = await asyncio.to_thread(
            fine_tune,
            ctx.base,
            train,
            val,
            config,
            device_request=ctx.device,
            should_stop=cancelled.is_set,
            on_progress=on_progress,
        )
        await _progress(ctx, org_id, job_id, {"phase": "evaluating", "steps": trained.steps})
        tokenizer, base_model = await asyncio.to_thread(ctx.base.load)
        label = f"kronos-{ctx.base.variant}"
        candidate = forecaster_from(
            tokenizer,
            trained.model,
            variant=ctx.base.variant,
            max_context=ctx.base.max_context,
            name="candidate",
            card="candidate",
            lookback=None,
        )
        reference = forecaster_from(
            tokenizer,
            base_model.eval(),
            variant=ctx.base.variant,
            max_context=ctx.base.max_context,
            name=label,
            card=label,
            lookback=None,
        )
        gate = await asyncio.to_thread(
            run_gate, series, candidate, reference, config, min_improvement=ctx.min_improvement
        )
        model_id = uuid.uuid4()
        files = await asyncio.to_thread(_save, trained.model)
        key = org_key(str(org_id), "models", str(model_id))
        for name, data in files.items():
            ctx.blobs.put(f"{key}/{name}", data)
        await registry.add_candidate(
            ctx.db,
            org_id,
            model_id=model_id,
            name=f"{label}-ft-{model_id.hex[:6]}",
            base_model=label,
            artifact_key=key,
            sha256=artifact_digest(files),
            job_id=job_id,
            metrics={
                "steps": trained.steps,
                "device": trained.device,
                "train_loss": trained.train_loss[-5:],
                "val_loss": trained.val_loss,
                "best_val_loss": trained.best_val_loss,
                "windows": len(train),
                "warnings": report.warnings,
                "train_seconds": round(trained.seconds, 2),
            },
        )
        outcome = await registry.record_gate_outcome(ctx.db, org_id, model_id, gate)
        log.info("job %s: model %s %s", job_id, model_id, outcome)
        status = "succeeded"
    except TrainingCancelledError:
        status = "cancelled"
    except DatasetError as exc:
        error = str(exc)[:MAX_ERROR_CHARS]
    except Exception as exc:  # a job may fail for any reason; the customer sees a short message
        log.exception("job %s failed", job_id)
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        cancelled.set()
        watcher.cancel()
    seconds = time.perf_counter() - started
    async with ctx.db.tenant(org_id) as conn:
        await conn.execute(
            "UPDATE finetune_jobs SET status = CASE WHEN status = 'cancelled' THEN status "
            "ELSE $2 END, error = $3, gpu_seconds = $4, model_id = $5, finished_at = now() "
            "WHERE id = $1",
            job_id,
            status,
            error,
            round(seconds, 2),
            model_id,
        )
    await ctx.meter.record_actual(
        org_id,
        "gpu_seconds",
        max(1, round(seconds)),
        idempotency_key=f"ft-{job_id}",
        meta={"job": str(job_id), "device": ctx.device},
    )
    return status


async def work_once(ctx: WorkerContext, pool: str) -> bool:
    """Claim and run one job if there is one. Returns whether a job was run."""
    claimed = await claim(ctx, pool)
    if claimed is None:
        return False
    org_id, job_id = claimed
    await run_job(ctx, org_id, job_id)
    return True


async def work_forever(ctx: WorkerContext, pool: str, poll_seconds: float = 5.0) -> None:
    while True:
        if not await work_once(ctx, pool):
            await asyncio.sleep(poll_seconds)


def main() -> None:  # pragma: no cover - a thin CLI over work_forever
    from tycheon_cp.blob import LocalBlobStore  # noqa: PLC0415
    from tycheon_cp.config import Settings  # noqa: PLC0415
    from tycheon_cp.db import Database  # noqa: PLC0415
    from tycheon_cp.metering import Meter  # noqa: PLC0415
    from tycheon_cp.plans import load_plans  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Tycheon Cloud fine-tune worker")
    parser.add_argument("--pool", default="shared")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument("--variant", default="mini", choices=["mini", "small", "base"])
    parser.add_argument("--once", action="store_true", help="run at most one job, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    async def go() -> None:
        settings = Settings.from_env()
        db = await Database.connect(settings.database_url)
        ctx = WorkerContext(
            db=db,
            blobs=LocalBlobStore(settings.storage_root),
            meter=Meter(db, load_plans()),
            base=PinnedKronos(args.variant),
            device=args.device,
        )
        try:
            if args.once:
                await work_once(ctx, args.pool)
            else:
                await work_forever(ctx, args.pool)
        finally:
            await db.close()

    asyncio.run(go())


if __name__ == "__main__":  # pragma: no cover
    main()
