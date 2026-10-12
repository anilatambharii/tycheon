# Launch checklist and remaining risks

Written at the end of Phase T6 (2026-10-07). It separates what was **run and passed on a developer
machine** from what **has never run** (anything needing a cloud account, a live Stripe key, a GPU, a
browser session or a person). Re-run the audit any time with `deploy/ops/final_audit.sh`.

For research and risk analytics. Not investment advice.

## 1. Phase T6 acceptance

| Acceptance criterion | Result | Evidence |
|---|---|---|
| `terraform plan` OK with example vars | **Passed, offline.** AWS complete example: 108 resources to add; AWS customer-VPC: 75; GCP: 87. `fmt`, `init`, `validate` pass for all five roots | `deploy/terraform/verify.sh`, re-run independently; no credentials used, **no `apply` ever run** |
| `helm install` healthy on kind (CPU profile) | **Passed.** All pods Ready, `helm test` passes, sign-up and forecast through the control plane and the open-source API, `401` without a key, MCP plan gate (`403`), dashboard `200`, model-cache prefetch Job filled the volume with the pinned Kronos-mini checkpoint, every running container has a read-only root, no capabilities, non-root | `deploy/helm/kind-smoke.sh` |
| Staging deploy green | **Not verified.** There is no cloud account or cluster. The staging and production jobs in `images.yml` are written, use `helm --atomic`, `helm test` and an HTTP smoke test, and are skipped until `STAGING_ENABLED` / `PRODUCTION_ENABLED` and a kubeconfig are configured. The same chart was installed on kind instead | `.github/workflows/images.yml` |

## 2. Re-run of the acceptance checks from T0 to T5 (2026-10-07)

| Stage | Result |
|---|---|
| Lint, format, `mypy --strict` on `src/` | pass |
| Fast suite (T0-T4: data, models, calibration, risk, backtest, leakage, agents, serving, evals) | **1,242 passed, coverage 95.45%** (gate 85%). The first run had one failure, a docs-navigation order test broken by this phase's new nav entries; fixed and the whole suite re-run |
| Examples: `forecast.py`, `risk_report.py`, `agentic_risk_review.py` (T4 acceptance: verifier rejection and revision, approval request, 0 orders executed, audit chain verified), MCP smoke | pass |
| Package build, `twine check`, no proprietary code in the wheel | pass (107 files, none from `ee/`) |
| Docs build (`mkdocs --strict`) | pass |
| Cloud (T5): `mypy --strict` on `ee/`; **299 tests on a real Postgres** (isolation, metering, billing fixtures, SSO, fine-tune gate, migrations, retention, metrics) | pass |
| T5 acceptance script (sign-up, CSV, forecast, risk, real Kronos-mini fine-tune on CPU, usage) | pass, **except the Stripe invoice-preview step, which needs a test key and did not run** |
| Backup/restore drill (dump, drop, restore, verify data and security properties) | pass, 13/13 checks |
| Dashboard: typecheck, lint, 47 tests, build | pass |
| Helm lint and Kubernetes schema check, four profiles, two Kubernetes versions | pass |
| Terraform fmt, validate, offline plan | pass |
| Images: three CPU images built, scanned, SBOM generated, hardened-runtime checks (8 each); CUDA variant built | pass (the CUDA image is not scanned or run: see section 4) |

Two findings of this phase's own checks, both fixed: the scanner flagged **three HIGH CVEs in
`cryptography` 47.0.0** (used for the credential envelope encryption), fixed by requiring 50.x; and a
unit test caught an **invalid GitHub Actions YAML** (an unquoted colon in a step name) before it could
break the pipeline.

## 3. Images and supply chain

| Image | Size | Scan gate (fixable HIGH/CRITICAL) | Known unfixed findings |
|---|---|---|---|
| `tycheon-api` | 2.56 GB | pass | Debian base: 53 HIGH, 2 CRITICAL, none with a published fix |
| `tycheon-cloud` | 2.61 GB | pass | same base-image findings |
| `tycheon-dashboard` | 0.45 GB | pass (no fixable findings) | Debian base: 48 HIGH, 1 CRITICAL, none with a published fix |

The two CRITICAL findings are in the Debian 12 base: SQLite `CVE-2025-7458` (affected, no fixed version
published) and zlib `CVE-2023-45853` (Debian marks it *will not fix*; it concerns `minizip`, which Tycheon
does not use). The gate fails the build on any HIGH or CRITICAL that **has a fix**; unfixed base-image
findings are disclosed, not hidden, and are re-scanned on every build. The HIGH findings are mostly in
`util-linux` packages of the base image. A distroless or Chainguard base would remove most of them: worth
evaluating (the images are large mainly because of PyTorch, not the OS).

Signing, the SPDX SBOM attestation and signature verification are in `images.yml` but **have not run**:
they need GitHub Actions and the registry. The SBOMs here were generated locally with Syft (233 to 236
packages for the Python images, 225 for the dashboard).

## 4. What has never run

* **Any real cloud:** `terraform apply`, the ALB with its WAF, IRSA / workload identity, RDS or Cloud SQL
  behaviour, quotas and name collisions, GPU node availability and scale-from-zero, the GCP plan with
  real credentials, cluster autoscaler, EFS/Filestore for shared storage.
* **The CUDA image on a GPU:** it was built locally (9.76 GB; PyTorch 2.6.0+cu124 with the CUDA 12.4 libraries
  present, no GPU visible on this machine) but was not scanned, and nothing has run on a GPU. Fine-tuning has
  only ever run on CPU (real Kronos-mini, 8 steps).
* **Live Stripe:** nothing has talked to Stripe (the code refuses live keys; a test key is needed).
* **Signing and pushing images**, and the staging and production deploy jobs.
* **NetworkPolicy enforcement:** the policies render and validate, but kind's default network plugin does
  not enforce them. Test with a policy-enforcing CNI before relying on them.
* **The alert rules** pass `promtool check rules` but have never evaluated real traffic, and the GPU and
  cost alerts need the DCGM exporter and kube-state-metrics, which the chart does not install.
* **Browser use** of the dashboard, **SSO with a real identity provider**, and the `--llm anthropic|openai|ollama`
  paths of the agentic example (no model key was used).
* **The CI workflows of this phase on GitHub:** they are linted and YAML-checked, and every action SHA was
  confirmed to exist, but they have not run.

## 5. Risk register

| # | Risk | Impact | Status / mitigation |
|---|---|---|---|
| 1 | `main` is **not protected** (no required reviews or status checks), `CONTRIBUTING.md` says it is; no CODEOWNERS. *(Update: branch protection and Dependabot security updates were enabled after this was written, and `.github/dependabot.yml` for version updates was added.)* | anyone with write access can push to `main` and bypass every check | **Fix before launch** (settings, below) |
| 2 | 40 actions in the older workflows are pinned by tag, and `publish.yml` uses a branch ref (`pypa/gh-action-pypi-publish@release/v1`) | supply-chain exposure; Scorecard "Pinned-Dependencies" high | list in `docs/launch/scorecard-audit.md`; the new workflows are pinned by SHA |
| 3 | Nothing deployed anywhere; no real load, backup or failover has been exercised | unknown operational behaviour | stage first; run the restore drill and a failover on real infrastructure |
| 4 | Stripe path unverified live; pricing numbers marked *default* in `plans.toml` are unconfirmed | wrong billing | give a test key, run `ee/scripts/acceptance_t5.py`; confirm the numbers |
| 5 | Calibration decay and forecast drift are only **proxied** by served-calibration metrics; no scheduled evaluation against realised outcomes | quality regressions could go unnoticed | add the nightly evaluation job that publishes coverage and CRPS as metrics |
| 6 | Evidence of model quality is synthetic: on the bundled series **no model beats the random walk**, and the README says so | credibility if over-claimed | keep the honest framing; publish a real-data benchmark with licensed data before making any performance claim |
| 7 | Rate limiting is per process; storage is a local filesystem volume (no S3); a dead fine-tune worker leaves its job `running`; quotas are hard limits with no overage | scale, resilience, billing | documented gaps in `docs/cloud.md` |
| 8 | No on-call rota, pager, status page, security mailbox, DPA, privacy notice, or vendor review; SOC 2 is *aligned*, not certified | customer trust, legal | see `docs/ops/soc2-controls.md`; these are organisational tasks |
| 9 | Base-image findings without a published fix | residual vulnerabilities | disclosed; rescanned per build; consider a minimal base |
| 10 | No way yet to delete an organisation and all its data on request | privacy obligations (GDPR erasure) | build the endpoint and runbook before taking EU customers |
| 11 | The `psycopg` (LGPL) dependency in the open-source `serve` extra, from Phase T0 | licence compliance for the Apache-2.0 package | decide: keep, or move that code to asyncpg |
| 12 | Management actions are audited but not Keelgate tool calls | governance coverage is uneven | recorded in ADR 0009 |

## 6. Before you announce (in this order)

**Settings only you can change**
1. Protect `main`: require a pull request, the required checks (`check (py3.11)`, `check (py3.12)`,
   `make check`, `pre-commit`, `cloud tests`, `dashboard`, `helm`, `terraform`, `kind`, `gitleaks`,
   `detect-secrets`), and at least one review; then correct the sentence in `CONTRIBUTING.md` if you do not.
2. ~~Enable Dependabot alerts and security updates; add `.github/dependabot.yml`.~~ Done.
3. Create the `staging` and `production` environments (reviewers on production), set the variables and
   secrets listed in `docs/ops/cicd.md` once a cluster exists.
4. Enable GitHub Discussions and create the categories in `docs/launch/discussions.md`; add repository topics.
5. Run `docs/launch/create-issues.sh` (it is a dry run until you set `DRY_RUN=0`) after reviewing
   `docs/launch/good-first-issues.yaml`; create the labels from `docs/launch/labels.yaml`.
6. Check the OpenSSF Scorecard badge after the first run of `scorecard.yml` on `main`.

**Things to decide or supply**
7. A Stripe **test** key, to run the invoice preview and the catalogue creation.
8. The `default` numbers in `plans.toml`; the `psycopg` question; whether to publish the blog post
   (`docs/blog/leakage-proof-benchmark.md`, a draft with maintainer notes at the top).
9. A real cloud account for staging; then run `terraform apply` in a throwaway account first.

**Before real customers**
10. On-call, paging and a status page; the security contact in `SECURITY.md`; a DPA and privacy notice.
11. A restore drill on the real database; a failover test; a load test against the SLOs.
12. The nightly calibration/coverage evaluation job (risk 5); organisation deletion (risk 10).

## 7. Launch day
1. Merge the release PR; re-push the tag so the publish workflow runs (release-please tags do not trigger it);
   approve the `pypi` environment; verify the PyPI install in a clean environment.
2. Announce with the honest results: the benchmark table, including that the random walk wins on the
   published data, and the disclaimer.
3. Watch the issue tracker and Discussions; answer within a day for the first week.
