# Model cards

Every model Tycheon can route to gets a card, and every forecast references the card of
the model that produced it (`ForecastDistribution.model_card`). No card, no routing: a
contract test fails if a forecaster's card is missing or lacks a required section.

## The cards

| Forecaster id | What it is | Joint sample paths | Card |
|---|---|---|---|
| `kronos-mini` | Kronos, 4.1M parameters, 2048-bar context | yes | [Kronos mini](kronos-mini.md) |
| `kronos-small` | Kronos, 24.7M parameters, 512-bar context | yes | [Kronos small](kronos-small.md) |
| `kronos-base` | Kronos, 102.3M parameters, 512-bar context | yes | [Kronos base](kronos-base.md) |
| `timesfm-2.5-200m` | TimesFM 2.5, 200M parameters | no, marginal quantiles | [TimesFM](timesfm.md) |
| `chronos-2` | Chronos-2, 120M parameters | no, marginal quantiles | [Chronos-2](chronos-2.md) |
| `random-walk` | Driftless log-price random walk | yes | [Random walk](random-walk.md) |
| `drift` | Random walk with estimated drift | yes | [Drift](drift.md) |
| `seasonal-naive` | Seasonal random walk | yes | [Seasonal naive](seasonal-naive.md) |
| `arima` | ARIMA on log returns | yes | [ARIMA](arima.md) |
| `garch` | GARCH(1,1) on log returns | yes | [GARCH](garch.md) |

`Kronos-large` (499.2M parameters) is listed upstream as not publicly available, so it is
not supported and has no card.

## What a card states

Required headings, enforced by `tests/test_forecaster_contract.py`:

| Section | Contents |
|---|---|
| **Source** | Where the model and its code come from, the version or commit pinned, who to cite. |
| **License** | The licence of the weights and of the code, and what that means for redistribution. |
| **Training data** | What the *publisher* says it was trained on. Where the publisher says nothing, so do we. |
| **Limitations** | Known ways it misleads, including what Tycheon observed. |
| **Calibration** | Whether, how, and on what window its uncertainty has been checked. |

Cards also say what it is for, how Tycheon runs it, and how it compares with the baselines.

## Rules the cards follow

- **Measured, with the window.** A number on a card says what was measured, on what data,
  and on what machine. A single observation is called a single observation.
- **Publisher claims are attributed.** Training-data and licence statements are quoted from
  the publisher's own card and repository, with the date they were read. Tycheon does not
  restate them as its own findings.
- **No skill claims.** Until walk-forward evaluation exists (Phase T3), every card's
  baseline-comparison section says so. Nothing here asserts that any model beats the
  random walk.
- **Every forecast is uncalibrated** until Phase T2 adds conformal calibration; each card
  says so, and so does every `ForecastDistribution`.

For research and risk analytics. Not investment advice.
