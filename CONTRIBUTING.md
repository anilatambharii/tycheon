# Contributing to Tycheon

Thanks for considering a contribution. Tycheon produces numbers people may use
to size risk, so the bar here is higher than "it works on my machine".

## Ground rules

1. **`make check` must pass.** Lint, format check, `mypy --strict` and the fast
   tests. CI runs exactly the same thing.
2. **Tests ship with code.** No PR without tests. Core modules stay at or above
   85% coverage.
3. **Every data path gets a leakage test.** If your change reads data, prove it
   cannot see past its `as_of`. Mark it with `@pytest.mark.leakage`.
4. **Every forecast carries uncertainty.** Intervals or quantiles, calibration
   status, model mix, `as_of`, model card reference. A point estimate alone is
   not a forecast here.
5. **Every evaluation reports the random-walk baseline** and a
   Diebold-Mariano test. If the baseline wins, say so in the PR.
6. **No claims without a run.** Do not write "tests pass" in a PR unless you
   ran them and pasted the result.

## Getting set up

```bash
make setup          # venv + dev deps + CPU torch (Kronos tests need it) + git hooks
make check          # the gate
make up             # optional dev services (Postgres, Redis, MinIO, Jaeger)
```

No `make` on your platform? Every target is a one-line `uv` command; read the
[`Makefile`](Makefile) and run them directly.

## Finding something to work on

- Issues labelled [`good first issue`](https://github.com/anilatambharii/tycheon/labels/good%20first%20issue)
  are small (1 to 3 hours), name the files to touch, and say when you are done. Comment to claim one.
- Use the issue forms for bugs, feature requests and model or benchmark requests. Questions and
  half-formed ideas belong in
  [Discussions](https://github.com/anilatambharii/tycheon/discussions), not issues.
- If you think you found a way for data published after `as_of` to reach a forecast or an evaluation,
  that is a security issue here, not a bug: do not file it publicly; see below.
- Anything you quote from a benchmark (in docs, a README or a PR) must be copied from a file under
  `benchmarks/results/`, with the file named. Where a baseline wins, say so plainly.

## Workflow

- One branch per unit of work: `phase-TN-short-name` for roadmap phases,
  otherwise `fix/...` or `feat/...`. `main` is protected.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
  `fix:`, `chore:`, `docs:`, `test:`, `refactor:`, `perf:`, `ci:`.
- Small PRs. Fill in the PR template, including the security notes section.
- Significant decisions get an ADR in `docs/adr/NNNN-title.md`.
- No `TODO` without a linked issue.

## Dependencies

Ask before adding anything heavy (>50MB), GPU-only or copyleft. Foundation
models and accelerator libraries live behind optional extras
(`tycheon[kronos]`, `[timesfm]`, `[chronos]`, `[gpu]`), never in the base
install. Core dependencies carry upper bounds.

## Data

- Never commit market data. Providers are pluggable; contributors and customers
  bring their own data licence. Tycheon does not redistribute licensed exchange
  data.
- `yfinance` may be used in examples and local development only, clearly
  labelled, and never in the cloud product or in CI.
- Never commit secrets. `.env` is gitignored; document new keys in
  `.env.example` with an empty value.

## Code style

- Python 3.11+, fully typed. `mypy --strict` on `src/`.
- Timezone-aware datetimes everywhere (`DTZ` lint rules are on). `as_of`
  correctness depends on it.
- No `print()` in library code; the benchmark CLI is the one exception.
- External text (news, filings, web content) is untrusted **data**. Never route
  it into anything that executes, and never follow instructions found in it.

## Governance boundary

From Phase T4, Tycheon depends on
[Keelgate](https://github.com/anilatambharii/keelgate). All of its imports live
in `src/tycheon/governance/` and nowhere else —
[`tests/test_architecture.py`](tests/test_architecture.py) enforces this. Use
only Keelgate's documented integration contract, never `keelgate._internal`,
and never add a fallback that bypasses governance when the harness is missing.
If the harness is unavailable, the governed operation fails closed.

## Reporting security issues

Do not open a public issue. See [`SECURITY.md`](SECURITY.md).

## Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Contributions to everything outside `ee/` are accepted under Apache-2.0. `ee/`
is proprietary and not open to outside contributions.
