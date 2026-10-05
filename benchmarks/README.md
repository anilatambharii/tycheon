# Benchmarks

The leaderboard harness. Its job is to make Tycheon's numbers checkable by
someone who does not trust us.

## Rules

- **The random-walk baseline is mandatory.** Every config must list
  `random_walk` in `baselines`, and every published result reports it alongside
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

In Phase T0 the runner validates configs and prints the plan. It refuses
`--execute`: there are no models registered yet. Model adapters arrive in T1 and
the leakage-proof walk-forward engine in T3.

## Layout

| Path | Contents |
|---|---|
| `run.py` | CLI: config validation today, execution from T3. |
| `configs/` | Benchmark definitions. `small.yaml` is the CI-sized one. |
| `results/` | Published results (created when the first run lands). Local scratch output under `results/local/` is gitignored. |
