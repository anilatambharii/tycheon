"""The Kronos fine-tuning loop.

Same objective as upstream Kronos pre-training: the tokenizer (frozen) turns each normalised
window into a pair of token streams, and the model is trained to predict the next token of both
streams (cross-entropy, teacher forcing on the first stream). Only the forecasting model is
updated; the tokenizer is never changed, so a fine-tuned model is served with the same pinned
tokenizer as the base.

PyTorch is imported lazily: the control plane never needs it, only the fine-tune workers do.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

    from tycheon_ft.dataset import FineTuneConfig, WindowSet


class TrainingCancelledError(Exception):
    """The job was cancelled while training."""


class BaseModels(Protocol):
    """Where the base tokenizer and model come from (the pinned Kronos checkpoint, or a test's)."""

    variant: Any
    max_context: int

    def load(self) -> tuple[Any, Any]:
        """A fresh ``(tokenizer, model)`` pair on CPU. The model is the one to be trained."""
        ...


@dataclass
class TrainResult:
    model: Any
    steps: int
    seconds: float
    device: str
    train_loss: list[float] = field(default_factory=list)
    val_loss: list[float] = field(default_factory=list)
    best_val_loss: float | None = None
    stopped_early: bool = False


def resolve_device(request: str) -> str:
    import torch  # noqa: PLC0415 - optional heavy dependency

    if request == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if request not in ("cpu", "cuda", "mps"):
        raise ValueError(f"unknown device {request!r}")
    return request


def _batches(
    windows: WindowSet, batch_size: int, rng: np.random.Generator
) -> list[tuple[Any, Any]]:
    order = rng.permutation(len(windows))
    return [
        (windows.x[order[i : i + batch_size]], windows.stamps[order[i : i + batch_size]])
        for i in range(0, len(order), batch_size)
        if len(order[i : i + batch_size]) >= 2
    ]


def _loss(tokenizer: Any, model: Any, x: Any, stamp: Any, torch: Any) -> Any:
    with torch.no_grad():
        tokens = tokenizer.encode(x, half=True)
    s1, s2 = tokens[0], tokens[1]
    s1_logits, s2_logits = model(
        s1[:, :-1], s2[:, :-1], stamp[:, :-1], use_teacher_forcing=True, s1_targets=s1[:, 1:]
    )
    loss, _, _ = model.head.compute_loss(s1_logits, s2_logits, s1[:, 1:], s2[:, 1:])
    return loss


def evaluate_loss(
    tokenizer: Any, model: Any, windows: WindowSet, device: str, batch_size: int
) -> float | None:
    import torch  # noqa: PLC0415

    if len(windows) == 0:
        return None
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for i in range(0, len(windows), batch_size):
            x = torch.from_numpy(windows.x[i : i + batch_size]).to(device)
            s = torch.from_numpy(windows.stamps[i : i + batch_size]).to(device)
            if len(x) < 2:
                continue
            total += float(_loss(tokenizer, model, x, s, torch)) * len(x)
            count += len(x)
    return total / count if count else None


def fine_tune(
    base: BaseModels,
    train: WindowSet,
    val: WindowSet,
    config: FineTuneConfig,
    *,
    device_request: str = "auto",
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> TrainResult:
    """Train a copy of the base model on ``train``; return the best-validation weights."""
    import torch  # noqa: PLC0415

    device = resolve_device(device_request)
    torch.manual_seed(config.seed)
    rng = np.random.default_rng(config.seed)
    tokenizer, model = base.load()
    tokenizer = tokenizer.to(device).eval()
    for parameter in tokenizer.parameters():
        parameter.requires_grad_(False)
    model = model.to(device)
    optimiser = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=0.01)

    started = time.perf_counter()
    result = TrainResult(model=model, steps=0, seconds=0.0, device=device)
    best_state: dict[str, Any] | None = None
    batch = config.batch_size
    for epoch in range(config.epochs):
        model.train()
        for x_np, s_np in _batches(train, batch, rng):
            if should_stop and should_stop():
                raise TrainingCancelledError
            x = torch.from_numpy(x_np).to(device)
            stamp = torch.from_numpy(s_np).to(device)
            loss = _loss(tokenizer, model, x, stamp, torch)
            if not torch.isfinite(loss):
                raise FloatingPointError("the training loss became non-finite")
            optimiser.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            result.steps += 1
            result.train_loss.append(float(loss.detach()))
            if result.steps >= config.max_steps:
                break
        val_loss = evaluate_loss(tokenizer, model, val, device, batch)
        if val_loss is not None:
            result.val_loss.append(val_loss)
            if result.best_val_loss is None or val_loss < result.best_val_loss:
                result.best_val_loss = val_loss
                best_state = copy.deepcopy(
                    {k: v.detach().cpu() for k, v in model.state_dict().items()}
                )
        if on_progress:
            on_progress(
                {
                    "epoch": epoch + 1,
                    "steps": result.steps,
                    "train_loss": result.train_loss[-1:],
                    "val_loss": result.val_loss[-1:],
                }
            )
        if result.steps >= config.max_steps:
            result.stopped_early = epoch + 1 < config.epochs
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    result.model = model.cpu()
    result.seconds = time.perf_counter() - started
    return result
