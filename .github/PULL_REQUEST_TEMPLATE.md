## What and why

<!-- What changes, and what problem it solves. Link the issue or the phase. -->

Phase / issue:

## How it was tested

<!-- Paste the real output. `make check` must have been run, not assumed. -->

```text
$ make check

```

- [ ] `make check` passes locally (lint, format, `mypy --strict`, fast tests)
- [ ] Tests ship with this change
- [ ] Core coverage is still at or above 85%
- [ ] Docs and examples updated, or not applicable

## Correctness rules touched by this PR

Tick what applies and say where it is tested.

- [ ] **Point-in-time** — every new data read takes an `as_of` and refuses data
      published after it, with a leakage test (`@pytest.mark.leakage`)
- [ ] **Uncertainty** — any new forecast output carries intervals/quantiles,
      calibration status, model mix, `as_of` and a model card reference
- [ ] **Baselines** — any new evaluation reports the random-walk baseline and a
      Diebold-Mariano test (state the result, including if the baseline won)
- [ ] **Untrusted text** — external text (news, filings, web) is treated as
      data, never as instructions
- [ ] **Governance** — no Keelgate import outside `src/tycheon/governance/`,
      and no fallback that bypasses governance
- [ ] None of the above apply

## Security notes

<!-- Required. "None" is a valid answer, but say it deliberately. Consider:
     new dependencies and their licences, new network calls, new secrets or env
     vars, anything that touches as_of handling, anything that could leak
     licensed market data, anything that widens the ee/ boundary. -->

## Dependencies

- [ ] No new dependencies
- [ ] New dependency added — justified below, upper-bounded, not copyleft, and
      heavy models kept behind an optional extra

## Open items

<!-- Anything deliberately left undone. No `TODO` in code without a linked issue. -->
