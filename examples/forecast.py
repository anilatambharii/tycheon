"""Kronos forecast distribution beside the random-walk baseline, on one sample series.

    uv sync --extra kronos --group examples
    uv run python examples/forecast.py                       # Kronos-small, CPU or GPU
    uv run python examples/forecast.py --variant mini        # smaller and faster
    uv run python examples/forecast.py --data-dir my_csvs --symbol MYSYM

What it shows, in order:

1. Bars are read **point-in-time** from the as-of store. The last ``horizon`` bars are
   held out: the forecast is made as of the moment the bar before them became known,
   so the held-out bars cannot leak into it.
2. Kronos and the random walk each forecast the same horizon from the same history.
   Both return a distribution, not a line: quantiles, and (for these two) joint paths.
3. A table compares them against what actually happened, and a plot is saved.

Read the result with care. The default series is **synthetic**, one series is not
evidence of skill, and zero-shot Kronos output is uncalibrated: its interval is
whatever the pre-trained model believes, not a measured coverage. The point of the
example is the plumbing and the honest presentation of uncertainty.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from tycheon.data import AsOfStore, SampleProvider
from tycheon.data.providers.file import FileProvider
from tycheon.models.base import DISCLAIMER, ForecastDistribution
from tycheon.models.baselines import RandomWalkForecaster
from tycheon.models.kronos import KronosForecaster


def load_history(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    """Return ``(history, held_out, as_of)`` read through the as-of store."""
    provider = FileProvider(args.data_dir) if args.data_dir else SampleProvider()
    far_future = pd.Timestamp("2100-01-01", tz="UTC")
    with tempfile.TemporaryDirectory() as tmp:
        store = AsOfStore(tmp)
        store.ingest(provider, args.symbol, start=None, end=None, as_of=far_future)
        everything = store.bars(args.symbol, as_of=far_future)
        cut = len(everything) - args.horizon
        if cut < 64:
            raise SystemExit(f"{args.symbol} has too few bars for a horizon of {args.horizon}")
        as_of = pd.Timestamp(everything["available_at"].iloc[cut - 1])
        # Read again *as of* the cut: this is the history a forecaster could have had.
        history = store.bars(args.symbol, as_of=as_of)
        held_out = everything.iloc[cut:]
    return history, held_out, as_of


def describe(label: str, d: ForecastDistribution, realized: np.ndarray) -> list[str]:
    lo, hi = d.interval(d.max_coverage)
    inside = float(np.mean((realized >= lo) & (realized <= hi)))
    last = d.horizon - 1
    return [
        f"{label:<16}",
        f"{d.metadata.context_length_used:>5}",
        f"{d.median[0]:>9.2f}",
        f"{d.median[last]:>9.2f}",
        f"[{lo[last]:>8.2f}, {hi[last]:>8.2f}]",
        f"{hi[last] - lo[last]:>8.2f}",
        f"{inside:>6.0%}",
    ]


def plot(
    history: pd.DataFrame,
    held_out: pd.DataFrame,
    forecasts: dict[str, ForecastDistribution],
    *,
    out: Path,
    symbol: str,
    tail: int,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(forecasts), figsize=(6.2 * len(forecasts), 4.8), sharey=True)
    recent = history["close"].iloc[-tail:]
    for ax, (label, d) in zip(np.atleast_1d(axes), forecasts.items(), strict=True):
        ax.plot(recent.index, recent.to_numpy(), color="#333333", lw=1.4, label="history")
        ax.plot(
            held_out.index,
            held_out["close"].to_numpy(),
            color="#333333",
            lw=1.4,
            ls="--",
            label="what happened",
        )
        if d.samples is not None:
            for path in d.samples[: min(25, len(d.samples))]:
                ax.plot(d.index, path, color="#3b6ea8", lw=0.6, alpha=0.18)
        lo90, hi90 = d.interval(d.max_coverage)
        ax.fill_between(
            d.index, lo90, hi90, color="#3b6ea8", alpha=0.18, label=f"{d.max_coverage:.0%} interval"
        )
        lo50, hi50 = d.interval(0.5)
        ax.fill_between(d.index, lo50, hi50, color="#3b6ea8", alpha=0.32, label="50% interval")
        ax.plot(d.index, d.median, color="#1f4e8c", lw=2.0, label="median")
        ax.axvline(d.as_of, color="#999999", lw=0.8, ls=":")
        ax.set_title(f"{label}   [{d.calibration_status}]", fontsize=11)
        ax.tick_params(axis="x", rotation=30, labelsize=8)
        ax.grid(alpha=0.25)
    np.atleast_1d(axes)[0].set_ylabel(f"{symbol} close")
    np.atleast_1d(axes)[0].legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.suptitle(
        f"{symbol}: forecast distribution from the same history, "
        f"as of {next(iter(forecasts.values())).as_of:%Y-%m-%d}",
        fontsize=12,
    )
    fig.text(
        0.5,
        0.005,
        "Synthetic data unless --data-dir is used. One series is not evidence of skill. "
        f"{DISCLAIMER}",
        ha="center",
        fontsize=8,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--variant", choices=["mini", "small", "base"], default="small")
    parser.add_argument(
        "--symbol", default="SYN-GARCH", help="sample series, or a file under --data-dir"
    )
    parser.add_argument(
        "--data-dir", help="directory of <symbol>.csv files you are licensed to use"
    )
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--samples", type=int, default=50, help="paths drawn per model")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda or mps")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--tail", type=int, default=90, help="history bars to draw")
    parser.add_argument("--out", type=Path, default=Path("examples/output/forecast.png"))
    args = parser.parse_args(argv)

    history, held_out, as_of = load_history(args)
    realized = held_out["close"].to_numpy()
    print(
        f"series {args.symbol}: {len(history)} bars known as of {as_of:%Y-%m-%d}; "
        f"forecasting {args.horizon} more"
    )

    kronos = KronosForecaster(args.variant, device=args.device, seed=args.seed)
    walk = RandomWalkForecaster(seed=args.seed)
    forecasts = {
        f"Kronos-{args.variant}": kronos.predict(history, args.horizon, args.samples, as_of),
        "random walk": walk.predict(history, args.horizon, args.samples, as_of),
    }

    header = [
        "model",
        "ctx",
        "step 1",
        f"step {args.horizon}",
        "interval at last step",
        "width",
        "inside",
    ]
    print("\n" + "  ".join(f"{h:<16}" if i == 0 else f"{h:>9}" for i, h in enumerate(header)))
    for label, d in forecasts.items():
        print("  ".join(describe(label, d, realized)))
    print(
        f"\nlast close {history['close'].iloc[-1]:.2f}; "
        f"actual at step {args.horizon}: {realized[-1]:.2f}"
    )
    print("'inside' = share of the held-out steps inside each model's widest central interval.")
    for d in forecasts.values():
        print(d.summary())

    plot(history, held_out, forecasts, out=args.out, symbol=args.symbol, tail=args.tail)
    print(f"\nsaved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
