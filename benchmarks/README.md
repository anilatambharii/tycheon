# Benchmarks

The leaderboard harness. Its job is to make Tycheon's numbers checkable by
someone who does not trust us.

## Rules

- **The random-walk baseline is mandatory.** Every config must list
  `random-walk` in `baselines`, and every published result reports it alongside
  a Diebold-Mariano test. `benchmarks/run.py` rejects configs that skip it.
- **An embargo is mandatory.** `walk_forward.embargo` separates train from test
  so labels cannot leak backwards across a fold boundary.
- **Point-in-time or nothing.** Folds are evaluated with the `as_of` of their
  own test window. A run that cannot prove its inputs were available at `as_of`
  is not a result.
- **We publish when the baseline wins.** That is the whole point of having one.

## Running

```bash
make benchmark-small                                   # validate the smoke config
python -m benchmarks.run --config benchmarks/configs/small.yaml
```

Today the runner validates configs (model and baseline names are checked against the
real forecaster registry) and prints the plan. It refuses `--execute`: the
forecasters exist, but the leakage-proof walk-forward engine that would score them
arrives in Phase T3, and a runner that printed numbers without it would be
measuring nothing.

## Layout

| Path | Contents |
|---|---|
| `run.py` | CLI: config validation today, execution from T3. |
| `configs/` | Benchmark definitions. `small.yaml` is the CI-sized one. |
| `results/` | Published results (created when the first run lands). Local scratch output under `results/local/` is gitignored. |
