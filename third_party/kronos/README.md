# Vendored: Kronos

Upstream: <https://github.com/shiyu-coder/Kronos> at commit
`67b630e67f6a18c9e9be918d9b4337c960db1e9a`, MIT licensed
(Copyright (c) 2025 ShiYu). The licence file is retained here verbatim and ships
inside the wheel.

## What is here

| File | Why |
|---|---|
| `model/kronos.py` | `KronosTokenizer`, `Kronos`, the sampling helpers. |
| `model/module.py` | The transformer building blocks they import. |
| `LICENSE` | Upstream MIT licence. Must travel with the code. |
| `UPSTREAM.json` | Commit and SHA-256 of every file above. |

Upstream's `model/__init__.py`, predictor examples, fine-tuning pipeline and web
UI are deliberately **not** vendored. Tycheon does not call upstream's
`KronosPredictor`: its sampler averages the sample paths away, and Tycheon needs
them (see ADR 0002).

## Rules

1. **Do not edit these files.** `tests/test_vendored_kronos.py` hashes them
   against `UPSTREAM.json` and fails on any difference, so an accidental edit,
   an editor reformat or a line-ending change cannot slip in.
2. Everything Tycheon adds lives in `src/tycheon/models/kronos/`, never here.
3. To update, vendor the new upstream commit in a PR of its own, regenerate
   `UPSTREAM.json`, and re-run the slow Kronos tests. Read upstream's diff
   first: a changed sampler or normalisation is a changed model.
4. Excluded from ruff, mypy and every pre-commit hook, because reformatting
   vendored code makes it impossible to diff against upstream.

## How it is imported

Upstream does `from model.module import *` behind a `sys.path.append("../")`.
Putting a top-level package called `model` on `sys.path` would shadow anything
else with that name, so `tycheon.models.kronos.vendor` loads the two files by
path and resolves that one import itself, then removes every trace of it.
