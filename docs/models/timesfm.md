# TimesFM 2.5

Forecaster id `timesfm-2.5-200m` · `tycheon.models.timesfm.TimesFMForecaster`

## Source

Google Research's TimesFM, checkpoint `timesfm-2.5-200m`
([`google/timesfm-2.5-200m-pytorch`](https://huggingface.co/google/timesfm-2.5-200m-pytorch)),
pinned to Hugging Face commit `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`. Code from the PyPI
package `timesfm` 3.0.2 (`TimesFM_2p5_200M_torch`). Paper: *A decoder-only foundation model
for time-series forecasting*, ICML 2024 ([arXiv:2310.10688](https://arxiv.org/abs/2310.10688)),
which the checkpoint card cites. The card describes it as the third open TimesFM checkpoint and
as "not an officially supported Google product".

Read 2026-10-04 from the Hugging Face card and the 3.0.2 sources: 200M parameters; a model
context limit of 16384; input patches of 32 and output patches of 128; `forecast` returns
`(point, quantiles)` with quantiles shaped `(series, horizon, 10)`: the mean, then the 10th to
90th percentiles.

## License

Apache-2.0 for both the weights (Hugging Face metadata) and the code (PyPI metadata).

## Training data

As stated by the publisher on the checkpoint card (read 2026-10-04): GiftEvalPretrain;
Wikimedia Pageviews with a November 2023 cutoff; Google Trends top queries with an end-of-2022
cutoff; and synthetic and augmented data. These are general-purpose time series. The card does
not describe financial price series as a focus, and Tycheon does not claim that it was trained
on any.

## Intended use

Zero-shot forecasting of a univariate series. Tycheon feeds it the **close price level** (not
returns), most recent `max_context` bars (default 1024), and requests the compiled
`max_horizon` (default 128).

## Limitations

- **Marginal quantiles only.** TimesFM says what each step might be, not how steps move
  together. `samples` is `None` and `require_paths()` refuses path-dependent questions such as
  drawdown probability; Tycheon does not fabricate a correlation structure.
- **Native quantile levels are 0.1 to 0.9.** The widest central interval available is 80%
  (`ForecastDistribution.max_coverage`), and nothing is extrapolated into the tails: levels
  such as 1% or 99%, used by VaR and Expected Shortfall, are not available from this model
  directly.
- Marginal quantiles can cross; Tycheon sorts them and records
  `diagnostics["quantile_crossing_repaired"]` when it does (the compiled model is asked to
  fix crossing as well).
- Chosen device: TimesFM selects CUDA when present and otherwise CPU; there is no switch, so
  `device` is read back into the metadata rather than set.
- `max_horizon` is fixed at compile time; a longer request raises instead of recompiling.
- Prices are positive, so the model is compiled with `infer_is_positive=True`.
- No covariates are used by this adapter.

## Calibration

Uncalibrated. The quantiles are the model's own. Conformal calibration arrives in Phase T2.

## How Tycheon runs it

`from_pretrained(..., revision=<pinned SHA>, torch_compile=False)` then `compile(ForecastConfig(
max_context, max_horizon, normalize_inputs=True, use_continuous_quantile_head=True,
force_flip_invariance=True, infer_is_positive=True, fix_quantile_crossing=True))`. The adapter
is tested against fakes that reproduce the engine's call and output contract, and against the
real weights in the nightly slow suite (see the verification note below).

## Baseline comparison

Not yet evaluated. Walk-forward evaluation against the random walk, with a Diebold-Mariano
test, arrives in Phase T3; nothing here claims TimesFM beats it.

## Verification note

On 2026-10-04 the adapter was run against the real `google/timesfm-2.5-200m-pytorch` weights (925 MB) with `timesfm` 3.0.2, CPU only, on synthetic data: output shape, finiteness, non-crossing quantiles and positive medians held (`tests/test_foundation_slow.py`). That checks the plumbing and the layout assumptions (mean first, then the 0.1 to 0.9 quantiles), not forecast quality.

For research and risk analytics. Not investment advice.
