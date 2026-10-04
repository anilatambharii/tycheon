# Chronos-2

Forecaster id `chronos-2` · `tycheon.models.chronos.ChronosForecaster`

## Source

Amazon's Chronos-2 ([`amazon/chronos-2`](https://huggingface.co/amazon/chronos-2)), pinned to
Hugging Face commit `29ec3766d36d6f73f0696f85560a422f50e8498c`, run through the PyPI package
`chronos-forecasting` 2.3.2 (`Chronos2Pipeline`). Technical report:
[arXiv:2510.15821](https://arxiv.org/abs/2510.15821).

Read 2026-10-04 from the Hugging Face card: a 120M-parameter, encoder-only model; univariate,
multivariate and covariate-informed tasks in one architecture; maximum context 8192 and maximum
prediction length 1024; multi-step quantile forecasts; CPU and GPU inference.

## License

Apache-2.0 for both the weights (Hugging Face metadata) and the code.

## Training data

As stated by the publisher (read 2026-10-04): a combination of real-world and large-scale
synthetic datasets, with `autogluon/chronos_datasets` and `Salesforce/GiftEvalPretrain` listed
on the card. These are general-purpose time series; the card does not describe financial price
series as a focus, and Tycheon does not claim that it was trained on any.

## Intended use

Zero-shot forecasting of a univariate series. Tycheon feeds it the **close price level**, the
most recent `max_context` bars (default 2048), and asks for a configurable set of quantile
levels (default 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95). Longer horizons are handled by the
package's own autoregressive unrolling.

## Limitations

- **Marginal quantiles only.** No joint sample paths; `samples` is `None` and `require_paths()`
  refuses path-dependent questions, exactly as for [TimesFM](timesfm.md).
- Quantile levels are limited to what is requested and must lie inside (0, 1); nothing is
  extrapolated beyond the levels asked for.
- Quantile crossing, if it occurs, is repaired by sorting and recorded in the diagnostics.
- Chronos-2 can use covariates (past and known-future); this adapter does not, until the
  covariates work in a later phase.
- Device: the requested device (`auto`, `cpu`, `cuda`, `mps`) is passed as `device_map`; an
  unavailable accelerator is an error, not a silent CPU fallback.
- Long contexts and horizons cost time and memory; the context is capped at `max_context`.

## Calibration

Uncalibrated. The quantiles are the model's own. Conformal calibration arrives in Phase T2.

## How Tycheon runs it

`Chronos2Pipeline.from_pretrained("amazon/chronos-2", revision=<pinned SHA>, device_map=<device>)`
then `predict_quantiles([close], prediction_length=horizon, quantile_levels=...)`. The adapter
is tested against fakes that reproduce the pipeline's call and output contract, and against the
real weights in the nightly slow suite (see the verification note below).

## Baseline comparison

Not yet evaluated. Walk-forward evaluation against the random walk, with a Diebold-Mariano
test, arrives in Phase T3; nothing here claims Chronos-2 beats it.

## Verification note

On 2026-10-04 the adapter was run against the real `amazon/chronos-2` weights (478 MB) with `chronos-forecasting` 2.3.2, CPU only, on synthetic data: output shape, finiteness, non-crossing quantiles and positive medians held (`tests/test_foundation_slow.py`). That checks the plumbing, not forecast quality.

For research and risk analytics. Not investment advice.
