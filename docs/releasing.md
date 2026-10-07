# Releasing

How a Tycheon release is cut. Nothing here is automatic for the first release: v0.1.0 is
tagged by a maintainer after the checks below, because publishing to PyPI is not reversible.

## One-time setup (maintainers)

1. **PyPI trusted publisher.** On pypi.org (Account settings, Publishing), add a *pending
   publisher* for the project `tycheon`: owner `anilatambharii`, repository `tycheon`, workflow
   `publish.yml`, environment `pypi`. No API token is created or stored; GitHub proves the
   workflow identity to PyPI with OIDC. Check first that the name `tycheon` is free.
2. **GitHub environment.** Settings, Environments, create `pypi` and add yourself under
   *Required reviewers*, so every real upload waits for a human approval.
3. **GitHub Pages.** Settings, Pages, source "GitHub Actions". The `Docs` workflow then
   publishes the strict-built site on every push to `main`.
4. **release-please.** Allow GitHub Actions to create pull requests
   (Settings, Actions, General, "Allow GitHub Actions to create and approve pull requests").

TestPyPI is optional. If you want a dry run, add a trusted publisher on test.pypi.org
(environment `testpypi`), create that GitHub environment, and start the `Publish` workflow
by hand: a manual run uploads to TestPyPI only and never to PyPI. A tag release does not
depend on it.

## Cutting v0.1.0

PyPI does not allow a version to be uploaded twice, even after a delete or yank. If 0.1.0
turns out to be broken the fix is 0.1.1, so check before tagging.

1. `main` is green and contains the release content. `CHANGELOG.md` says `0.1.0`; replace
   "Unreleased" with the date.
2. Verify the distribution locally, from a clean checkout of `main`:

   ```bash
   uv build
   uv venv /tmp/rel --python 3.12
   uv pip install --python /tmp/rel/bin/python "tycheon[report] @ file://$PWD/dist/tycheon-0.1.0-py3-none-any.whl"
   cd "$(mktemp -d)" && cp -r /path/to/repo/examples . \
     && /tmp/rel/bin/python examples/risk_report.py --out out
   ```

   (On Windows the interpreter is `Scripts/python.exe`.) The `Publish` workflow repeats the
   metadata check (`twine check --strict`) and a wheel import smoke test before uploading.
3. Tag and push: `git tag -a v0.1.0 -m "v0.1.0" && git push origin v0.1.0`. The workflow
   verifies the tag equals the version in `pyproject.toml`, builds, checks the metadata,
   smoke-tests the wheel, then waits for your approval of the `pypi` environment before
   publishing to PyPI. Read the approval prompt: this step cannot be undone.
4. Verify what users get, in a clean environment:

   ```bash
   python -m venv /tmp/v && /tmp/v/bin/pip install "tycheon[report]==0.1.0"
   /tmp/v/bin/python -c "import tycheon; print(tycheon.__version__)"
   ```
5. Create the GitHub release from the tag (paste the changelog section).

## After 0.1.0

release-please keeps a release PR open: it bumps `pyproject.toml` and `CHANGELOG.md` from
Conventional Commits (`feat:` minor, `fix:` patch, `feat!:` or `BREAKING CHANGE:` major,
while below 1.0 a breaking change bumps the minor). Merging it creates the tag and release;
the tag triggers `Publish`.

## Releasing 0.2.0 and the Keelgate dependency

The `agents` and `serve` extras declare `keelgate>=0.1,<0.2`. Keelgate is **not on PyPI yet**, and
in this repository its source is pinned by commit through `[tool.uv.sources]`, which is uv-only and
not part of published package metadata. So a published `tycheon[agents]` or `tycheon[serve]` cannot
be installed until a Keelgate release in that range is on PyPI. `pip install tycheon` (the base
package) is unaffected.

Before uploading 0.2.0: publish Keelgate 0.1.x to PyPI first (or accept that those two extras do not
install until you do, and say so in the release notes). Verify with a clean virtual environment:
`pip install "tycheon[agents,report]==0.2.0"` and run `examples/agentic_risk_review.py`.

## What a release must not contain

Secrets, licensed market data, or results computed from licensed data without the owner's
permission. Published benchmark results are synthetic.
