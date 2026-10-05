# Residual-corrected forecaster

Forecaster id `residual(<base id>)` · `tycheon.covariates.ResidualCorrectedForecaster`

A base forecaster plus a model of its errors from as-of-stamped covariates (news sentiment,
fundamentals, macro series). It sits behind a `ResidualCorrector` interface so a better fusion
method can replace the first implementation without touching callers.

## Source

Implemented in Tycheon. `FeatureBuilder` computes each feature row only from covariate
versions published by that row's time. `LightGBMResidualCorrector` trains
[LightGBM](https://github.com/microsoft/LightGBM) (native API) to predict the base model's
realised log-return residual, per horizon step, on a chronological train/validation/test
split. The correction is **accepted only if it beats a zero correction on the untouched test
split by `min_improvement`**; otherwise the base forecast passes through unchanged and the
report says so.

## License

Tycheon: Apache-2.0. LightGBM: MIT.

## Training data

The caller's covariate store and bars. Covariate values are numeric only, stored bi-temporally
(`timestamp`, `available_at`, optional `key`; revisions are later versions). News text is never
read by this code: only numeric scores derived elsewhere are accepted, so instructions inside
a headline cannot reach it.

## Intended use

Testing whether covariates add information beyond price, with an honest answer when they do
not.

## Limitations

- Residual correction is the simplest fusion; it cannot represent interactions with the
  base model's internals.
- Small samples overfit: the acceptance rule guards this but does not remove it.
- It refuses an `as_of` earlier than its training data (that would be lookahead).
- Sentiment scores, fundamentals and macro series are only as point-in-time as the caller
  stamped them.

## Calibration

Uncalibrated as built; wrap it with `ConformalCalibrator`. A correction shifts the centre of
the distribution, and calibration then measures whether the intervals around it hold.

## Baseline comparison

The zero-correction (base) forecast is the baseline in the acceptance test. Evaluation against
the random walk with Diebold-Mariano tests arrives in Phase T3; not yet measured.

For research and risk analytics. Not investment advice.
