# Kronos base

Forecaster id `kronos-base` · `tycheon.models.kronos.KronosForecaster("base")`

## Source

- **Model:** [`NeoQuasar/Kronos-base`](https://huggingface.co/NeoQuasar/Kronos-base),
  pinned to Hugging Face commit `2b554741eca47781b64468546e77fef3e85130e6`.
- **Tokenizer:** [`NeoQuasar/Kronos-Tokenizer-base`](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base), pinned to commit
  `0e0117387f39004a9016484a186a908917e22426`. Each Kronos model must be paired with its own tokenizer.
- **Code:** upstream [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos), vendored
  byte-identical at commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a` in `third_party/kronos`
  ([ADR 0002](../adr/0002-kronos-integration.md)). Tycheon replaces upstream's sampler so the
  sample paths are kept rather than averaged; a test shows the replacement matches upstream to
  the bit under greedy decoding.
- **Paper:** Shi et al., *Kronos: A Foundation Model for the Language of Financial Markets*,
  [arXiv:2508.02739](https://arxiv.org/abs/2508.02739). The upstream README notes acceptance at
  AAAI 2026. Tycheon cites the paper as the authority on the model; it has not independently
  re-derived its claims.

Facts below are from the upstream README, the Hugging Face cards and `config.json`, read
2026-10-04: 102.3M parameters; context length 512; a decoder-only transformer (d_model 832, feed-forward 2048, 16 heads, 12 layers);
K-lines are quantised by the tokenizer into hierarchical discrete tokens (two 10-bit streams,
`s1_bits` = `s2_bits` = 10; the tokenizer uses group size 4) before the transformer models
them autoregressively.

## License

MIT, for both the weights (Hugging Face metadata) and the code (upstream `LICENSE`, retained at
`third_party/kronos/LICENSE` and shipped inside the Tycheon wheel). Tycheon itself is Apache-2.0;
the vendored files keep their own notice.

## Training data

As stated by the publisher on the Hugging Face card (read 2026-10-04): Kronos is "trained on data
from over 45 global exchanges", on "a massive, multi-market corpus of over 12 billion K-line
records". The card does not state the date range, the frequency mix or the split between asset
classes, and does not say what the model has never seen; the paper is the place to look. Tycheon
makes no claim beyond these publisher statements.

## Intended use

Zero-shot forecasting of candlestick series (open, high, low, close, volume, amount). Tycheon
feeds it the most recent `min(len(history), lookback, 512)` bars, forecasts all six channels,
returns the **close** as the forecast target, and keeps the other channels in
`ForecastDistribution.extras`. Missing `volume` and `amount` are filled the way upstream does.

## Limitations

- **Zero-shot and uncalibrated.** The spread of the sample paths is whatever the pre-trained
  model believes. Nothing has checked that a 90% interval contains the outcome 90% of the time.
- **Not anchored to the last close.** The forecast is decoded from tokens, and the tokenizer is
  lossy. On one synthetic series (`SYN-GARCH`, 900 bars known) the tokenizer round-trip of
  the last close was off by -0.40%, and the median of the first forecast step sat
  +2.76% from the last close, against a daily volatility of about 1.2% on that series.
  This is the model's behaviour, not a porting error (the port is bit-identical to upstream).
  Measuring and correcting it belongs to calibration.
- **Spread, one series.** Across 20 sample paths the standard deviation of
  `log(P[10] / P[0])` was 0.0171, against 0.0336 for the random walk's volatility
  scaled to the same horizon (ratio 0.51x). With 20 paths that standard deviation
  carries roughly +-16% sampling error, and it is one series.
- **Context.** The model was trained for 512-bar contexts; Tycheon never feeds it more and
  cuts longer histories to the most recent bars *before* normalising, so the statistics describe
  what the model reads (upstream normalises over everything it is handed). The forecast horizon
  must not exceed 512 bars: upstream decodes only the last 512 tokens, so a longer horizon
  would silently return the wrong shape, and Tycheon raises instead.
- **Frequencies and markets are not specified.** The cards do not say which bar sizes it was
  trained on. Calendar features (minute, hour, weekday, day, month) are read from the history's own
  timezone; for intraday data pass bars in the exchange's local time.
- **Upstream quirks Tycheon refuses to inherit:** upstream ignores `top_p` when `top_k > 0`
  (Tycheon rejects the combination), never calls `.eval()` (Tycheon does, because dropout left on
  would add noise), and averages its sample paths into one (Tycheon keeps them).
- **Cost.** CPU-only, no GPU: 236 s for 20 paths over a 512-bar context and a
  10-step horizon, plus 3 s to load, on the development machine
  (Windows 10 Pro, CPU only, PyTorch 2.14.1). Cost grows with paths, context and horizon; CUDA and Apple MPS are supported by
  the device switch but were **not tested** on any accelerator.

## Calibration

Uncalibrated (`calibration_status="uncalibrated"`). Conformal calibration and reliability
diagnostics arrive in Phase T2; until then treat the intervals as the model's opinion, not a
measured coverage.

## How Tycheon runs it

Weights and tokenizer load with `from_pretrained(..., revision=<pinned SHA>)` from
`safetensors` files (no pickle, so loading executes no code), are moved to the chosen device and
put in eval mode with gradients off. Sampling exposes `temperature`, `top_k` and `top_p`
(default `T=1.0`, `top_k=0`, `top_p=0.9`, as upstream) and an explicit seed driving a
`torch.Generator`; the seed and every setting are recorded in the forecast metadata.
Series are batched in chunks of `max_batch_rows` paths, and `predict_batch` accepts several
series of differing lengths (upstream requires equal lengths).

## Baseline comparison

Not yet evaluated. Walk-forward evaluation against the random walk, with a Diebold-Mariano test,
arrives in Phase T3. Nothing on this card claims that Kronos-base beats the random walk.

For research and risk analytics. Not investment advice.
