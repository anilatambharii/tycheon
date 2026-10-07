# OpenSSF Scorecard audit

**For research and risk analytics. Not investment advice.** (This page is about repository hygiene, not forecasts.)

Prepared for the maintainer before launch. Method: I read the repository's workflows, Dockerfiles, compose file, pre-commit config and policy files, and made a few **read-only** calls to the GitHub API (`gh api`) for settings that cannot be seen in the files. I did **not** run the Scorecard tool itself, so the scores below are my reading of each check's rules, not a measured result. The new `.github/workflows/scorecard.yml` will produce the real numbers on its first run; compare them with this page.

Severity is my judgement of the risk to this project (High, Medium, Low, Info), not Scorecard's weighting.

## Summary

| Area | Result |
|---|---|
| Looks good | License, Security-Policy, SAST (CodeQL), Dangerous-Workflow, Binary-Artifacts, CI-Tests, Packaging, Docker base images pinned by digest, default workflow token is read-only, secret scanning and push protection are on |
| Fix before launch | **Branch-Protection** (the API reports `main` as not protected, though `CONTRIBUTING.md` says it is), **Pinned-Dependencies** (40 unpinned action references), **Dependency-Update-Tool** (none configured) |
| Will not pass yet, by nature | Maintained (repo created 2026-10-04; the check needs 90 days of history), Contributors, CII-Best-Practices, Fuzzing, Signed-Releases, Code-Review |

## Check by check

### Branch-Protection: High

- Found: `gh api repos/anilatambharii/tycheon/branches/main/protection` returned HTTP 404 "Branch not protected" at the time of the audit. No rulesets were listed. `CONTRIBUTING.md` says "`main` is protected".
- Why it matters: without protection, anyone with write access (or a compromised token) can push to `main`, bypass CI, and publish through the tag-triggered `publish.yml`.
- Recommendation: add a branch ruleset (or classic protection) for `main`: require a pull request, require the status checks from `ci.yml` (and `docs`, `codeql`), block force pushes and deletion, and require at least one approval once there is a second maintainer. Until it is in place, change the `CONTRIBUTING.md` sentence so it states what is true.

### Pinned-Dependencies: High (supply chain), easy to fix

- Found: all 40 third-party `uses:` references in the existing workflows use mutable tags or a branch. Only the new `scorecard.yml` is pinned. Findings by file (no workflow other than `scorecard.yml` was edited):

| File | Unpinned reference (count) |
|---|---|
| `.github/workflows/ci.yml` | `actions/checkout@v4` (5), `astral-sh/setup-uv@v4` (5), `actions/upload-artifact@v4` (2) |
| `.github/workflows/codeql.yml` | `actions/checkout@v4`, `github/codeql-action/init@v3`, `github/codeql-action/analyze@v3` |
| `.github/workflows/dependency-review.yml` | `actions/checkout@v4`, `actions/dependency-review-action@v4` |
| `.github/workflows/docs.yml` | `actions/checkout@v4`, `astral-sh/setup-uv@v4`, `actions/upload-pages-artifact@v3`, `actions/deploy-pages@v4` |
| `.github/workflows/ee.yml` | `actions/checkout@v4` (2), `astral-sh/setup-uv@v4`, `actions/setup-node@v4`; the service container `postgres:16` is a tag, not a digest |
| `.github/workflows/nightly-slow.yml` | `actions/checkout@v4`, `astral-sh/setup-uv@v4`, `actions/cache@v4`, `actions/upload-artifact@v4` |
| `.github/workflows/publish.yml` | `actions/checkout@v4`, `astral-sh/setup-uv@v4`, `actions/upload-artifact@v4`, `actions/download-artifact@v4` (2), `pypa/gh-action-pypi-publish@release/v1` (2, a **branch** ref) |
| `.github/workflows/release-please.yml` | `googleapis/release-please-action@v4` |
| `.github/workflows/secret-scan.yml` | `actions/checkout@v4` (2), `astral-sh/setup-uv@v4`, `gitleaks/gitleaks-action@v2` |

- Also found: `publish.yml` runs `uvx twine check --strict dist/*` with an unpinned `twine` (Low); the compose file's images are version tags, not digests (`docker-compose.dev.yml`, local development only, Low).
- Good: `Dockerfile` and `ee/dashboard/Dockerfile` pin base images by `@sha256` digest; Python dependencies resolve from `uv.lock` (CI sets `UV_FROZEN`), and the dashboard uses `package-lock.json` with `npm ci`.
- Recommendation: pin every `uses:` to a full commit SHA with the tag as a trailing comment (the pattern in `scorecard.yml`), starting with `publish.yml` and `release-please.yml`, which hold release privileges. Pair it with Dependabot (below) so pins do not rot. Resolve annotated tags to commits: `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, and if `object.type` is `tag`, `gh api repos/<owner>/<repo>/git/tags/<sha>`. The good-first-issues file has three scoped issues for this.

### Token-Permissions: Medium

- Found: every workflow sets a top-level `permissions: contents: read` except `release-please.yml`, whose **top-level** block grants `contents: write` and `pull-requests: write`. `publish.yml`, `docs.yml` and `secret-scan.yml` grant extra scopes only at job level (`id-token: write`, `pages: write`, `pull-requests: write`), which is the recommended shape. `codeql.yml` grants `security-events: write` at job level.
- Repository setting (read via API): default workflow permissions are `read`, good. `can_approve_pull_request_reviews` is `true`: Actions may approve pull requests.
- Recommendation: move the two write scopes in `release-please.yml` to the job and keep the top level at `contents: read`; turn off "Allow GitHub Actions to create and approve pull requests" for approval, keeping only what release-please needs to open PRs.

### Dangerous-Workflow: Info (looks fine)

- Found: no `pull_request_target`, no `workflow_run`, and no `${{ github.event.* }}`, `github.head_ref` or similar attacker-controlled expressions interpolated into `run:` steps. `publish.yml` passes `GITHUB_REF_NAME` as an environment variable. The matrix value `${{ matrix.python-version }}` comes from a static list.
- Recommendation: keep it that way. If a workflow ever needs `pull_request_target`, never check out the PR head with write permissions.

### Security-Policy: Info (passes)

- Found: `SECURITY.md` exists with a private reporting route (GitHub Security Advisories), response times and a scope that treats lookahead leakage, uncertainty stripping and tenant crossing as vulnerabilities.
- Recommendation: confirm that private vulnerability reporting is enabled in the repository settings so the advisory link works for outsiders. The new issue-form `config.yml` points to the policy.

### License: Info (passes)

- Found: `LICENSE` (Apache-2.0) at the root, `license = "Apache-2.0"` in `pyproject.toml`, `ee/LICENSE` (proprietary) and `third_party/kronos/LICENSE` (MIT) scoped to their directories (ADR 0001).

### SAST: Info (passes, with a note)

- Found: `codeql.yml` runs CodeQL (Python, `security-extended`) on push, pull request and weekly. Ruff with security rules also runs in CI.
- Note: it uses `codeql-action@v3` while the new Scorecard workflow uses an upload-sarif v4 SHA; both work. Pin and update them together.

### Dependency-Update-Tool: Medium

- Found: no `.github/dependabot.yml` and no Renovate config. Repository setting: Dependabot security updates are **disabled**; the vulnerability-alerts endpoint returned 404, which I read as alerts being off.
- Recommendation: add `.github/dependabot.yml` for `github-actions` (and decide separately about Python, given the project's upper-bound policy), and enable Dependabot alerts and security updates in the repository settings. This is also what makes `dependency-review.yml` useful (it currently carries `continue-on-error` until the dependency graph is on; SECURITY.md tracks that in issue #2).

### Vulnerabilities: Info (not evaluated)

- Scorecard asks OSV about known vulnerabilities in dependencies. I did not run `osv-scanner` or `pip-audit`. Recommendation: run one against `uv.lock` and `ee/dashboard/package-lock.json` once and record the result.

### Binary-Artifacts: Info (passes)

- Found: the only tracked binary asset I found is `docs/assets/forecast-example.png`. `dist/` and `site/` exist in the working directory but are gitignored and untracked. No jars, executables or model weights are tracked (weights are downloaded and pinned by commit SHA in the specs files).

### CI-Tests: Info (passes)

- Found: `ci.yml` runs lint, format check, `mypy --strict`, and the fast tests on every pull request across Python 3.11 and 3.12, plus pre-commit, docs and packaging jobs.

### Packaging: Info (passes)

- Found: `publish.yml` publishes with `pypa/gh-action-pypi-publish` using trusted publishing (OIDC), behind a protected GitHub environment, as documented in `docs/releasing.md`. Scorecard should detect it.

### Signed-Releases: Low

- Found: the three GitHub releases (v0.1.0, v0.2.0, v0.3.0) have **no assets**, so there are no signatures or provenance files attached. The PyPI action may publish attestations itself; I did not verify that.
- Recommendation: attach a build provenance attestation (for example `actions/attest-build-provenance`) or signed artifacts to GitHub releases. Not needed for launch.

### Code-Review: Medium (cannot fully evaluate)

- Found: merges arrive as pull requests (`Merge pull request #15`, `#14`), and PR #14 shows reviews. With one or two contributors and no required approvals, Scorecard will score this low.
- Recommendation: require one approving review through branch protection once there is more than one maintainer; add a `CODEOWNERS` file (none exists) so reviewers are requested automatically.

### Maintained: Info (cannot pass yet)

- Found: the repository was created on 2026-10-04. Scorecard gives a low score to repositories younger than 90 days regardless of activity. No action needed; it resolves itself.

### Contributors: Low

- Found: 2 contributors through the API. The check wants contributors from several organisations. Resolves with community growth.

### CII-Best-Practices: Low

- Found: no OpenSSF Best Practices badge. Recommendation: register at bestpractices.dev and add the badge after launch; the existing CONTRIBUTING, SECURITY, CI and testing material answers most of the "passing" level questions.

### Fuzzing: Low

- Found: no OSS-Fuzz or ClusterFuzzLite. The test suite uses Hypothesis in `tests/test_governance.py`; I believe Scorecard may recognise Python property-based testing, but I did not verify that. A property test for `filter_as_of` is among the good first issues.

### Webhooks: Info

- Not applicable to a repository without webhooks configured; not inspected.

## Other observations (not Scorecard checks)

- **Repository topics are empty** (`gh api` returned `topics: []`). Adding topics (for example `forecasting`, `time-series`, `conformal-prediction`, `risk`, `quant`) helps discovery at launch.
- `can_approve_pull_request_reviews` is enabled; see Token-Permissions.
- Secret scanning and push protection are enabled; non-provider patterns and validity checks are not (optional).
- The new `scorecard.yml` uses `permissions: {}` at the top and grants `contents: read`, `security-events: write` and `id-token: write` to its one job. `id-token: write` is needed because `publish_results: true` publishes to the public Scorecard API, which backs the README badge. The badge will show nothing until the first run completes on `main`.

## Pinned versions used in `scorecard.yml`

Resolved with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`; annotated tags dereferenced to the commit, and each commit confirmed to exist in its repository.

| Action | Tag | Commit |
|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `ossf/scorecard-action` | v2.4.4 | `2d1146689b8cda280b9bc96326124645441f03bc` |
| `github/codeql-action/upload-sarif` | v4.38.2 | `2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2` |
