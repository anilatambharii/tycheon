# Releasing

How a Tycheon release is cut. Nothing here is automatic for the first release: v0.1.0 is
tagged by a maintainer after the checks below, because publishing to PyPI is not reversible.

## One-time setup (maintainers)

1. **PyPI and TestPyPI trusted publishers.** On pypi.org and test.pypi.org, add a *pending
   publisher* for the project `tycheon`: owner `anilatambharii`, repository `tycheon`, workflow
   `publish.yml`, environment `pypi` (PyPI) and `testpypi` (TestPyPI). No API token is created
   or stored; GitHub proves the workflow's identity to PyPI with OIDC.
2. **GitHub environments.** Create environments `testpypi` and `pypi`. Give `pypi` required
   reviewers so a human approves each real upload.
3. **GitHub Pages.** Settings, Pages, source "GitHub Actions". The `Docs` workflow then
   publishes the strict-built site on every push to `main`.
4. **release-please.** Allow GitHub Actions to create pull requests
   (Settings, Actions, General, "Allow GitHub Actions to create and approve pull requests").

## Cutting v0.1.0

1. `main` is green and contains the release content. `CHANGELOG.md` says `0.1.0`; replace
   "Unreleased" with the date.
2. Dry run to TestPyPI: run the `Publish` workflow manually (workflow dispatch). Then, in a
   clean virtual environment:

   ```bash
   python -m venv /tmp/t && /tmp/t/bin/pip install \
     --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ \
     "tycheon[report]==0.1.0"
   /tmp/t/bin/python -c "import tycheon; print(tycheon.__version__)"
   ```
3. Tag and push: `git tag -a v0.1.0 -m "v0.1.0" && git push origin v0.1.0`. The workflow
   verifies the tag equals the version in `pyproject.toml`, builds, checks the metadata,
   smoke-tests the wheel, publishes to TestPyPI, then waits for approval of the `pypi`
   environment before publishing to PyPI.
4. Create the GitHub release from the tag (paste the changelog section).

## After 0.1.0

release-please keeps a release PR open: it bumps `pyproject.toml` and `CHANGELOG.md` from
Conventional Commits (`feat:` minor, `fix:` patch, `feat!:` or `BREAKING CHANGE:` major,
while below 1.0 a breaking change bumps the minor). Merging it creates the tag and release;
the tag triggers `Publish`.

## What a release must not contain

Secrets, licensed market data, or results computed from licensed data without the owner's
permission. Published benchmark results are synthetic.
