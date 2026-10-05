# 0002. Kronos integration: vendor the model code, own the sampler

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** T1

## Context

Tycheon exists to put calibrated uncertainty around Kronos. That makes the Kronos
integration the one place where "build on the actual upstream API" matters most, so
this decision was made after reading the upstream source at commit `67b630e`, the
README, and the Hugging Face cards for the three open checkpoints and both
tokenizers. Four facts from that reading drive everything below.

1. **Upstream is not installable.** The repository has no `pyproject.toml` or
   `setup.py`, only a `requirements.txt`. `pip install git+https://...` cannot work,
   so "pin to an upstream git commit" is not available as a mechanism.
2. **Upstream's imports assume a checkout.** `model/kronos.py` does
   `sys.path.append("../")` and `from model.module import *`: a *relative* entry on
   `sys.path` and a top-level package called `model`. Neither is acceptable in a
   library.
3. **Upstream's requirements conflict with ours.** It pins `pandas==2.2.2`,
   `huggingface_hub==0.33.1` and `safetensors==0.6.2`; Tycheon resolves pandas 3.
4. **Upstream discards the distribution.** `auto_regressive_inference` draws
   `sample_count` paths and returns `np.mean(preds, axis=1)`: their *average*. A
   single averaged path is exactly what a calibration layer cannot use. Upstream also
   never calls `.eval()` (relying on the Hub mixin to do it), normalises over the
   whole input while the model reads only the last `max_context` rows, and for
   `top_k > 0` silently ignores `top_p`.

## Decision

1. **Vendor, byte-identical.** `third_party/kronos/` holds `model/kronos.py`,
   `model/module.py` and the upstream `LICENSE` (MIT, retained as AGENTS.md
   requires), copied at commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a`.
   `UPSTREAM.json` records the commit and the SHA-256 of every file; a test and the
   loader both verify it, so an edit, reformat or line-ending change cannot slip in.
   The files are excluded from ruff, mypy and every pre-commit hook for the same
   reason. A wheel ships them under `tycheon/_vendor/kronos` via hatch
   `force-include`.
2. **Load by path, restore everything.** `tycheon.models.kronos.vendor` executes the
   two files under unique module names, supplies the single `model.module` import
   upstream asks for through a temporary stand-in, and then restores `sys.path` and
   `sys.modules` exactly. A test asserts that nothing leaks, and that an
   application's own `model` module is not clobbered.
3. **Own the sampler.** `tycheon.models.kronos.sampling.sample_paths` is a port of
   upstream's loop that returns one path per row instead of averaging, uses an
   explicit `torch.Generator` (a seed makes a forecast reproducible without touching
   the global RNG), and imports the vendored `top_k_top_p_filtering` rather than
   re-implementing it. **Fidelity is tested, not asserted:** with `top_k=1` decoding
   is deterministic, and the port's output equals upstream's to the bit, on a tiny
   model (with and without a rolling window) and on the real Kronos-mini weights.
4. **Context policy.** The most recent `min(len, lookback, max_context)` bars are
   kept *before* normalisation, so the statistics describe what the model reads
   (`tycheon.models.kronos.windowing`). `horizon` must not exceed `max_context`;
   upstream would return the wrong shape rather than fail.
5. **Fail loudly where upstream is silent.** `top_k` with `top_p < 1` is rejected;
   the models are put in eval mode explicitly; a missing accelerator is an error,
   not a CPU fallback.
6. **Pinned, pickle-free weights.** Checkpoints are loaded from Hugging Face at exact
   commit SHAs (`tycheon.models.kronos.specs`), and are `safetensors`, so loading
   does not execute code and a re-pushed upstream repo cannot change a forecast.

## Consequences

**Good.** One deterministic, reviewable copy of the model code; no dependency on
upstream's packaging or its pandas pin; distributions instead of averages; every
deviation from upstream is deliberate, documented and tested.

**Costly.** We carry upstream's code and must update it by hand: a new upstream
commit means re-vendoring, regenerating `UPSTREAM.json`, reading the diff (a changed
sampler or normaliser is a changed model), and re-running the slow tests. The loader
is more machinery than an import statement. Kronos-large is not open, so it is not
supported.

**Open.** Upstream does not anchor forecasts to the last observation. On the one
synthetic series we checked, the tokenizer round-trip alone moved the last close by
+0.75%, and the first forecast step sat 1.7% above it. That is the model's behaviour,
not a porting error (the port is bit-identical), and measuring and correcting it
belongs to calibration (Phase T2).

## Alternatives considered

- **Git dependency on upstream.** Impossible: no packaging metadata.
- **Fork and patch upstream.** Fixes the imports and the averaging in place, but the
  vendored files then differ from upstream, so "is this still Kronos?" can no longer
  be answered by a hash, and every upstream update becomes a merge.
- **Call `KronosPredictor.predict(sample_count=1)` once per path.** Works without our
  own sampler and gives independent paths. Rejected because it is `n_samples` times
  slower (no batching of rows through the transformer) and re-encodes the history for
  every path; it would also keep upstream's global RNG. The greedy-equality test
  shows our sampler agrees with upstream, which this alternative could not improve.
- **Reimplement Kronos from the paper.** Unjustified risk and effort for a model whose
  weights are tied to this exact architecture.
- **Track `main` on the Hub.** Rejected: a forecast must be reproducible from a
  version, not from whatever the repository says today.
