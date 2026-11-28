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
5. **The adapters compute in a child process.** On Linux, importing certain native libraries after
   `regopy` is loaded aborts the process: DuckDB and torch on Python 3.12 and 3.14, and matplotlib
   (reached from `tycheon.metrics` through `arch`) on 3.14. `keelgate eval run` loads `regopy`
   before it loads plugins, so with the metrics computed in-process it aborted. The adapter module
   imports only Keelgate and the standard library, `tycheon.governance` loads its other names
   lazily, and `tycheon._metrics_worker` does the work in a clean process. A worker that fails
   raises. Tests run the real command and check what loading the adapters imports.

## Consequences

- `tycheon[agents]` and `tycheon[serve]` can be installed from PyPI once a Tycheon release carries
  this pin.
- Computing these metrics under Keelgate needs the `agents` extra, because loading an adapter
  loads `tycheon.governance`.
- `report.json` contains `Infinity` for a metric with no data. Python reads it; a strict JSON
  parser does not.
- Each metric starts one Python process, which imports the analytics stack again. This is slower,
  and acceptable for offline evaluation.
- A process that loads `keelgate.policy` before DuckDB still cannot use Tycheon's stores or models
  on Linux. This change does not alter that.

## Alternatives considered

Achieved coverage as the value (rejected: see decision 3); raising or `NaN` for a batch with no
Tycheon records (decision 4); deferring only the DuckDB import (tried first: it passed on Python
3.12, and CI on 3.14 then crashed importing matplotlib, because the set of libraries that conflict
is not something Tycheon controls); asking Keelgate to load `regopy` only for the suites that need
it (worth doing upstream, but Tycheon must work with 0.2.0 as released).
