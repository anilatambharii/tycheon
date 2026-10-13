"""Write SIMULATED daily-bar CSVs in the layout ``user-csv-daily`` expects.

This exists to exercise the bring-your-own-data path end to end (CSV reader, conservative
``available_at`` default, survivorship warning, redistribution flag) without any licensed data.
The numbers are random draws, **not market data**: nothing run on them says anything about
markets. Differences from the bundled generator, so the CSV path sees different shapes:

* Student-t(5) innovations with GARCH(1,1) volatility (fat tails and clustering);
* a business-day calendar with random missing days (holidays, halts);
* realistic OHLC around the close, and volume correlated with volatility;
* ``SIM-MR`` has a weak mean-reverting component (lag-1 autocorrelation about -0.08), the one
  place a model *could* legitimately beat the random walk, so the harness can be seen to say so.

Usage: ``python -m benchmarks.simulate_csv --out DIR [--seed N]``
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS: dict[str, dict[str, float]] = {
    "SIM-A": {"mu": 0.0003, "omega": 2e-6, "alpha": 0.07, "beta": 0.91, "phi": 0.0},
    "SIM-B": {"mu": 0.0001, "omega": 5e-6, "alpha": 0.12, "beta": 0.85, "phi": 0.0},
    "SIM-MR": {"mu": 0.0002, "omega": 3e-6, "alpha": 0.06, "beta": 0.90, "phi": -0.08},
    "SIM-C": {"mu": 0.0004, "omega": 8e-6, "alpha": 0.10, "beta": 0.88, "phi": 0.02},
}


def simulate(name: str, *, n_days: int, seed: int) -> pd.DataFrame:
    p = SYMBOLS[name]
    rng = np.random.default_rng(seed)
    nu = 5.0
    scale = np.sqrt((nu - 2) / nu)  # unit-variance Student-t
    var = p["omega"] / (1 - p["alpha"] - p["beta"])
    ret = np.zeros(n_days)
    sig = np.zeros(n_days)
    prev = 0.0
    for t in range(n_days):
        sig[t] = np.sqrt(var)
        eps = rng.standard_t(nu) * scale
        ret[t] = p["mu"] + p["phi"] * prev + sig[t] * eps
        var = p["omega"] + p["alpha"] * (sig[t] * eps) ** 2 + p["beta"] * var
        prev = ret[t]
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[100.0], close[:-1]]) * np.exp(rng.normal(0, sig * 0.3))
    span = np.abs(rng.normal(0, sig * 0.8))
    high = np.maximum(open_, close) * np.exp(span)
    low = np.minimum(open_, close) * np.exp(-span)
    volume = (1e6 * np.exp(rng.normal(0, 0.25, n_days) + 20 * sig)).round()
    days = pd.bdate_range("2017-01-02", periods=int(n_days * 1.04))
    keep = np.sort(rng.choice(len(days), size=n_days, replace=False))  # drop ~4% of days
    return pd.DataFrame(
        {
            "timestamp": days[keep].strftime("%Y-%m-%d"),
            "open": open_.round(4),
            "high": high.round(4),
            "low": low.round(4),
            "close": close.round(4),
            "volume": volume,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--days", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(SYMBOLS):
        path = args.out / f"{name}.csv"
        simulate(name, n_days=args.days, seed=args.seed + i).to_csv(path, index=False)
        print(f"wrote {path} (SIMULATED, not market data)")


if __name__ == "__main__":
    main()
