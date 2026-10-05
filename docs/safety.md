# Safety and compliance

These rules are not aspirational. They come from `AGENTS.md`, they are enforced
in tests and CI, and a PR that breaks one does not land.

## Point-in-time or nothing

Every data-reading function takes an `as_of` and refuses data published after
it. Every data path ships with a leakage test (`@pytest.mark.leakage`).

Lookahead leakage is treated as a **security vulnerability**, not a bug: it
silently inflates every downstream result, and the output still looks
plausible. See
[SECURITY.md](https://github.com/anilatambharii/tycheon/blob/main/SECURITY.md).

Practical consequences:

- timestamps are timezone-aware everywhere (the `DTZ` lint rules are on);
- vendor restatements are resolved as of the forecast time, not as of today;
- calibration sets are drawn strictly before the forecast `as_of`;
- walk-forward folds carry an embargo between train and test.

## No live execution

v1 has **no live brokerage execution**. Paper and simulation only, and from
Phase T4 only through Keelgate-governed tools. `TYCHEON_ALLOW_LIVE_EXECUTION`
ships `false` and there is no supported path that sets it true.

## Every forecast carries its uncertainty

Intervals or quantiles, calibration status, model mix, `as_of` and a model card
reference travel with the forecast. Stripping them in transport, in the API or
in the dashboard is a defect, and a security issue when it changes how a number
would be acted on.

## Honest evaluation

Every evaluation reports the random-walk baseline and a Diebold-Mariano test,
and we publish when the baseline wins. Numbers come with the window, the cost
assumptions and the number of configurations tried.

## External text is untrusted data

News, filings, transcripts and web content are **data**. They are parsed for
features and never followed as instructions. From T4, agent tools treat
retrieved text the same way, and no retrieved string can authorise an action.

## Data licensing

- Market-data providers are pluggable. Customers bring their own data licence.
- Tycheon never redistributes licensed exchange data, and no market data is
  committed to this repository.
- `yfinance` is for examples and local development only, always clearly
  labelled, gated behind `TYCHEON_ALLOW_YFINANCE`, and never used in the cloud
  product or in CI.
- Sample data shipped for tests and examples is synthetic or explicitly
  redistributable.

## Governance boundary (from Phase T4)

[Keelgate](https://github.com/anilatambharii/keelgate) is the safety harness:
capability-scoped tools, deterministic policy gates, approvals, tamper-evident
audit and evals.

- Phases T0 to T3 do not depend on Keelgate at all.
- From T4, every Keelgate import lives in `src/tycheon/governance/` and nowhere
  else, enforced by `tests/test_architecture.py`.
- Only the documented integration contract is used, never `keelgate._internal`.
- There is **no fallback that bypasses governance**. If the harness is
  unavailable, the governed operation fails closed.

## User-facing disclaimer

Every user-facing surface carries it:

> For research and risk analytics. Not investment advice.

## Secrets

`.env` is gitignored. `.env.example` is committed with every secret value
empty, and a test asserts it stays that way. Pre-commit blocks private keys;
CI runs detect-secrets and gitleaks on every PR and on a schedule.
