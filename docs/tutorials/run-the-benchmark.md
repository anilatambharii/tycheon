# Tutorial: run the benchmark

By the end of this page you will have run the leaderboard yourself, read a Diebold-Mariano
p-value against the random walk, and know how to point it at your own data.

**For research and risk analytics. Not investment advice.**

## 1. Install

```bash
git clone https://github.com/anilatambharii/tycheon && cd tycheon
make setup          # uv sync with CPU torch (for Kronos) and matplotlib
```

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or 3.12. No GPU is needed for the
small benchmark.

## 2. Run the small benchmark

```bash
make benchmark-small
```

This runs three synthetic series at a 5-bar horizon through a 3-fold walk-forward, with these
models: the random walk, a drift walk, seasonal naive, GARCH, Kronos-mini, the Tycheon regime
ensemble, and the Tycheon ensemble calibrated with adaptive conformal prediction. It takes a
few minutes on a laptop CPU and the first run downloads the small Kronos-mini weights from Hugging Face.

It writes `benchmarks/results/small/<version>.json` and renders `docs/leaderboard/index.md`.
Open the leaderboard with `make docs`.

## 3. Read the table

Start at the **random-walk row**: it is the baseline every other row is judged against.

| Column | How to read it |
|---|---|
| vs random walk | `beats RW` only if *both* Diebold-Mariano tests are below 0.05 one-sided. `= RW` means statistically indistinguishable. `worse than RW` means both tests say worse. |
| MASE | MAE over the random walk's MAE. Below 1 is better than doing nothing. |
| CRPS skill | `1 - CRPS / CRPS_random_walk`: positive means a better distribution than the random walk's. |
| Direction | Share of calls with the right sign. Noise gives about 50%. |
| DM p | The p-value that the model's loss is lower than the random walk's. Small is evidence of skill; large is not evidence of *no* skill, just no evidence. |

Then read **Where the baselines win**. It is generated from the numbers and lists how many
models beat the random walk, how many did not, and which headline metrics a baseline leads.
On the synthetic series most of these have no exploitable structure, so expect the random walk
to hold its own; a model that clearly beats it on a driftless random-walk series has
a leakage or overfitting problem, not a discovery.

The **interval coverage** table is where calibration shows: compare `tycheon-ensemble` with
`tycheon-calibrated`. The calibrated model's 90% interval should cover close to 90% of
outcomes. The raw models' intervals are whatever their models believe.

## 4. Check the leakage controls yourself

```bash
uv run pytest tests/test_backtest_walk_forward.py -k canary -q
```

These tests poison every price after a chosen origin and require forecasts at earlier origins
to be bit-identical, for the baselines, the ensemble and the calibrated forecaster. See the
[methodology](../benchmark-methodology.md) for the other controls.

## 5. Run everything

```bash
make benchmark      # all models, two horizons; needs the timesfm and chronos extras
```

Models whose extra is not installed show up under **Models that did not run**, with the exact
`pip install` that fixes it. They are never silently dropped.

## 6. Use your own data

Tycheon never ships or redistributes market data. Export daily bars from a source you are
licensed to use into a directory, one CSV per symbol (`AAA.csv`, ...), with columns
`timestamp, open, high, low, close, volume`. Add an `available_at` column if you know when each
bar became available; without it each bar is assumed known at the end of its bar, which is the
conservative default.

Copy `benchmarks/configs/small.yaml`, then:

```yaml
dataset: user-csv-daily
universe_kind: static          # or point_in_time, if your list is built that way
universe: [AAA, BBB]
```

```bash
uv run python -m benchmarks.run --config my.yaml --execute --data-dir ./my_csvs
```

Two things will happen that are deliberate: the run is marked *not redistributable* (publish
the results only if your licence allows), and a static universe of real symbols gets a
**survivorship-bias warning**, because a list chosen with hindsight contains only survivors.

### Try the CSV path without any data

`benchmarks/simulate_csv.py` writes **simulated** CSVs (fat-tailed GARCH returns, missing days, one
weakly mean-reverting series) so you can exercise the bring-your-own-data path end to end:

```bash
uv run python -m benchmarks.simulate_csv --out ./sim_csvs
uv run python -m benchmarks.run --config benchmarks/configs/simulated-csv.yaml --execute     --data-dir ./sim_csvs --out ./sim_results --no-render
```

A run of this on 2026-10-13 (4 series, horizon 5, 192 pooled origins, CPU, about 5 minutes, Tycheon
0.4.0, Kronos not included) printed both the survivorship and the not-redistributable warnings and
gave, against the random walk (MASE below 1 beats it; both Diebold-Mariano tests must have
p < 0.05 to count as a win):

| Model | MASE | 90% coverage | DM p (sq. error) | DM p (CRPS) | Outcome |
|---|--:|--:|--:|--:|---|
| random-walk | 1.019 | 91.1% | n/a | n/a | the reference |
| drift | 1.074 | 90.6% | 0.871 | 0.990 | indistinguishable |
| seasonal-naive | 1.033 | 90.6% | 0.714 | 0.919 | indistinguishable |
| arima | 1.063 | 92.7% | 0.859 | 0.991 | indistinguishable |
| garch | 1.033 | 90.6% | 0.795 | 0.048 | indistinguishable (one test only) |
| tycheon-ensemble | 1.026 | 90.1% | 0.630 | 0.080 | indistinguishable |
| tycheon-calibrated | 1.110 | 88.0% | 0.999 | 0.997 | **worse** than the random walk |

This is random data, so no model *should* win and none did; the useful facts are that the CSV path
works, the warnings fire, and the calibrated forecaster's interval coverage stayed near nominal
(88% for 90%) while its point error was worse than doing nothing. It says nothing about markets and
is not published to the leaderboard.

## 7. Add a model

Add `benchmarks/configs/models/<id>.yaml` (copy a neighbour) and list the id under `models:`
in your config. The model must implement the `Forecaster` protocol; the engine takes care of
leakage guards, origins and metrics.
