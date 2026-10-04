"""Autoregressive sampling for Kronos that **keeps every sample path**.

This is a port of upstream ``auto_regressive_inference`` with two deliberate
differences, and nothing else changed:

1. Upstream repeats the input ``sample_count`` times, generates all of them, then
   returns ``np.mean(preds, axis=1)``: the *average* of the paths. Averaging
   destroys exactly what Tycheon needs (a distribution), so rows are never averaged
   here. The caller decides how many rows to draw and gets each one back.
2. Randomness comes from an explicit ``torch.Generator`` instead of the global RNG,
   so a seed makes a forecast reproducible without disturbing anything else.

The model/tokenizer calls (``encode``, ``decode_s1``, ``decode_s2``, ``decode``), the
rolling context buffer, and the top-k / nucleus filtering are upstream's, and the
filter is imported from the vendored file rather than re-implemented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    import torch

# torch is imported inside the functions, not here: the base install has no torch,
# and importing this package (for example to list the models) must not require it.


def validate_sampling(temperature: float, top_k: int, top_p: float) -> None:
    """Reject sampling settings that upstream would accept and then quietly misapply."""
    if not temperature > 0:
        raise ValueError("temperature must be positive")
    if top_k < 0:
        raise ValueError("top_k must be >= 0 (0 disables it)")
    if not 0.0 < top_p <= 1.0:
        raise ValueError("top_p must be in (0, 1]")
    if top_k > 0 and top_p < 1.0:
        # Upstream returns straight after top-k and never applies top_p, so passing
        # both silently means "top_k only". Make that explicit instead.
        raise ValueError(
            "top_k and top_p cannot be combined: upstream Kronos applies top_k and ignores "
            "top_p when top_k > 0. Set top_k=0 to use top_p, or top_p=1.0 to use top_k."
        )


def _draw(
    logits: torch.Tensor,
    *,
    temperature: float,
    top_k: int,
    top_p: float,
    generator: torch.Generator,
    filter_fn: Callable[..., Any],
) -> torch.Tensor:
    import torch

    logits = logits / temperature
    if top_k > 0 or top_p < 1.0:
        logits = filter_fn(logits, top_k=top_k, top_p=top_p)
    probs = torch.softmax(logits, dim=-1)
    drawn: torch.Tensor = torch.multinomial(probs, num_samples=1, generator=generator)
    return drawn


def sample_paths(
    tokenizer: Any,
    model: Any,
    x: torch.Tensor,
    x_stamp: torch.Tensor,
    y_stamp: torch.Tensor,
    *,
    max_context: int,
    pred_len: int,
    clip: float,
    temperature: float,
    top_k: int,
    top_p: float,
    generator: torch.Generator,
    filter_fn: Callable[..., Any],
) -> torch.Tensor:
    """Generate one future path per row of ``x``.

    Args:
        x: ``(rows, context, 6)`` normalised history, one row per path to draw.
        x_stamp: ``(rows, context, 5)`` calendar features of the history.
        y_stamp: ``(rows, pred_len, 5)`` calendar features of the forecast bars.

    Returns:
        ``(rows, pred_len, 6)`` forecast in normalised units, one independent path
        per row. Nothing is averaged.
    """
    import torch

    with torch.inference_mode():
        x = torch.clip(x, -clip, clip)
        x_token = tokenizer.encode(x, half=True)

        initial_seq_len = x.size(1)
        batch_size = x_token[0].size(0)
        total_seq_len = initial_seq_len + pred_len
        full_stamp = torch.cat([x_stamp, y_stamp], dim=1)

        generated_pre = x_token[0].new_empty(batch_size, pred_len)
        generated_post = x_token[1].new_empty(batch_size, pred_len)

        pre_buffer = x_token[0].new_zeros(batch_size, max_context)
        post_buffer = x_token[1].new_zeros(batch_size, max_context)
        buffer_len = min(initial_seq_len, max_context)
        if buffer_len > 0:
            start_idx = max(0, initial_seq_len - max_context)
            pre_buffer[:, :buffer_len] = x_token[0][:, start_idx : start_idx + buffer_len]
            post_buffer[:, :buffer_len] = x_token[1][:, start_idx : start_idx + buffer_len]

        for step in range(pred_len):
            current_seq_len = initial_seq_len + step
            window_len = min(current_seq_len, max_context)

            if current_seq_len <= max_context:
                input_pre, input_post = pre_buffer[:, :window_len], post_buffer[:, :window_len]
            else:
                input_pre, input_post = pre_buffer, post_buffer

            context_start = max(0, current_seq_len - max_context)
            current_stamp = full_stamp[:, context_start:current_seq_len, :].contiguous()

            s1_logits, context = model.decode_s1(input_pre, input_post, current_stamp)
            sample_pre = _draw(
                s1_logits[:, -1, :],
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                generator=generator,
                filter_fn=filter_fn,
            )

            s2_logits = model.decode_s2(context, sample_pre)
            sample_post = _draw(
                s2_logits[:, -1, :],
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                generator=generator,
                filter_fn=filter_fn,
            )

            generated_pre[:, step] = sample_pre.squeeze(-1)
            generated_post[:, step] = sample_post.squeeze(-1)

            if current_seq_len < max_context:
                pre_buffer[:, current_seq_len] = sample_pre.squeeze(-1)
                post_buffer[:, current_seq_len] = sample_post.squeeze(-1)
            else:
                pre_buffer.copy_(torch.roll(pre_buffer, shifts=-1, dims=1))
                post_buffer.copy_(torch.roll(post_buffer, shifts=-1, dims=1))
                pre_buffer[:, -1] = sample_pre.squeeze(-1)
                post_buffer[:, -1] = sample_post.squeeze(-1)

        full_pre = torch.cat([x_token[0], generated_pre], dim=1)
        full_post = torch.cat([x_token[1], generated_post], dim=1)

        window_start = max(0, total_seq_len - max_context)
        decoded = tokenizer.decode(
            [
                full_pre[:, window_start:total_seq_len].contiguous(),
                full_post[:, window_start:total_seq_len].contiguous(),
            ],
            half=True,
        )
        paths: torch.Tensor = decoded[:, -pred_len:, :]
        return paths
