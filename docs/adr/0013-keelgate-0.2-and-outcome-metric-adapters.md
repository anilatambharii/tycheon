# 0013. Keelgate 0.2 and the outcome-metric adapters

- **Status:** Accepted (replaces decision 7 and the stand-in for outcome metrics in
  [0007](0007-keelgate-integration.md))
- **Date:** 2026-11-27

## Context

Keelgate 0.2.0 is on PyPI. It defines `OutcomeMetric` as `compute(records) -> MetricResult`, where
the value is always a float, and it runs the metrics in `keelgate eval run`. It also removed two
module paths Tycheon had imported (`keelgate.policy.engine`, `keelgate.adapters.governed`); both
names are public in the parent packages.

## Decision

1. **Depend on `keelgate>=0.2,<0.3` from PyPI.** The uv-only Git source is removed.
2. **Adapters in `tycheon.governance`, maths unchanged.** Each entry point names a class that
   rebuilds a `ScoreSet` from `OutcomeRecord`s and calls the class in `tycheon.metrics`. One
   record is one forecast origin; the format is documented in the adapter module.
3. **Coverage is reported as its gap to nominal.** Keelgate compares a metric against a baseline
   in one direction. Achieved coverage marked "lower is better" would count a fall from 90% to
   50% as an improvement. Achieved coverage stays in the details.
4. **No Tycheon records means `n=0` and an infinite value.** Keelgate's default run uses its own
   sample records, so raising would fail `keelgate eval run` wherever Tycheon is installed. `NaN`
   would pass every baseline comparison. Infinity is worse than any baseline, which every one of
   these lower-is-better metrics needs. Malformed Tycheon records raise.
5. **DuckDB is imported through `tycheon._duckdb`.** On Linux, importing DuckDB after `regopy` is
   loaded aborts the process. `keelgate eval run` loads `regopy` before it loads plugins, so with
   Tycheon installed it aborted with exit code 134. The import is still eager, except when
   `regopy` is already loaded; opening a store in that state on Linux raises `RuntimeError`.
   Tests run the real command in a child process.

## Consequences

- `tycheon[agents]` and `tycheon[serve]` can be installed from PyPI once a Tycheon release carries
  this pin.
- Computing these metrics under Keelgate needs the `agents` extra, because loading an adapter
  loads `tycheon.governance`.
- `report.json` contains `Infinity` for a metric with no data. Python reads it; a strict JSON
  parser does not.
- A process that loads `keelgate.policy` before Tycheon cannot open an as-of store on Linux. It
  now gets an error that says why, where it used to abort.

## Alternatives considered

Achieved coverage as the value (rejected: see decision 3); raising or `NaN` for a batch with no
Tycheon records (decision 4); making `tycheon.governance` import its modules lazily (a larger
change that would still leave the stores importing DuckDB); asking Keelgate to load `regopy` only
for the suites that need it (worth doing upstream, but Tycheon must work with 0.2.0 as released).
