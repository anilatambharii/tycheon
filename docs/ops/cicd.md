# CI/CD: build, scan, sign, push, deploy

Two workflows own delivery. Every action in them is pinned by commit SHA.

## `deploy-verify.yml` (no cloud account needed)
Runs on any change to `deploy/`, the Dockerfiles or the workflow itself.

| Job | What it proves |
|---|---|
| `helm` | `helm lint --strict` and `kubeconform -strict` (Kubernetes 1.29 and 1.31 schemas) for every profile, rendered with every optional component on |
| `terraform` | `terraform fmt -check`, `validate` and an **offline plan** with the example variables, for the AWS module, both AWS examples and the GCP module |
| `kind` | builds the three images, checks their security properties (`deploy/docker/verify_image.sh`), installs the chart on a throwaway kind cluster (CPU profile), runs `helm test`, then signs up an organisation and makes calls through the real services |

## `images.yml` (build, scan, sign, push, deploy)

```
pull request ──► build ─► scan ─► SBOM                                (nothing pushed)
push to main ──► build ─► scan ─► SBOM ─► push ─► sign + attest ─► verify ─► deploy staging
tag v*       ──► build ─► scan ─► SBOM ─► push ─► sign + attest ─► verify ─► deploy staging ─► [approval] ─► deploy production
```

* **Images**: `tycheon-api`, `tycheon-cloud` (CPU), `tycheon-cloud` `-cuda` (GPU workers), `tycheon-dashboard`
  at `ghcr.io/<owner>/`. Tags are `sha-<12 hex>`, `main`, and the release version; deploys use the
  **digest** the pipeline just signed, never a tag.
* **Scan**: Trivy fails the build on any HIGH or CRITICAL vulnerability **that has a fix**. Findings
  with no available fix (mostly in the Debian base image) are reported, not blocking: see the launch
  checklist for the current list.
* **SBOM**: SPDX from Syft, uploaded as an artifact and attached to the image as a signed attestation.
* **Signing**: `cosign sign` keyless with the workflow's OIDC identity: no key is stored. The pipeline
  immediately verifies the signature against that identity. To verify an image yourself:
  ```
  cosign verify ghcr.io/<owner>/tycheon-api@sha256:... \
    --certificate-identity-regexp '^https://github.com/<owner>/tycheon/\.github/workflows/images\.yml@' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  ```
* **Staging** deploys automatically from `main` (Helm `--atomic`: a failed upgrade rolls itself back),
  then `helm test` and an HTTP smoke test.
* **Production** deploys only from a `v*` tag, only after staging succeeded, and only after a reviewer
  approves the `production` environment. It takes a database snapshot first, deploys atomically,
  smoke-tests, and rolls back the release if the smoke test fails.

### Turning the deploys on
Both deploy jobs are **skipped, not failed**, until configured, because there is no cluster yet:

| Setting | Where | For |
|---|---|---|
| `STAGING_ENABLED=true`, `STAGING_URL`, `STAGING_DASHBOARD_URL` | repository variables | staging |
| `STAGING_KUBECONFIG` (base64) | `staging` environment secret | staging |
| `PRODUCTION_ENABLED=true`, `PRODUCTION_URL` | repository variables | production |
| `PRODUCTION_KUBECONFIG` (base64) | `production` environment secret | production |
| `PRODUCTION_RDS_INSTANCE`, `PRODUCTION_AWS_ROLE_ARN`, `PRODUCTION_AWS_REGION` | repository variables | pre-deploy snapshot (OIDC, no stored AWS keys) |
| Required reviewers | `production` environment settings | the manual approval |

Prefer short-lived cluster credentials (an OIDC role that can only `helm upgrade` in one namespace)
over a long-lived kubeconfig.

## Migrations and rollback
The Helm release runs `python -m tycheon_cp.cli migrate` as a pre-upgrade Job under the *owner*
database role; the running application uses an unprivileged role. A failed migration stops the
release before any new pod starts. Each migration has a tested `.down.sql`; `python -m tycheon_cp.cli rollback`
runs them, refusing any that drops tables without `--allow-data-loss`. See
[bad migration](runbooks/bad-migration.md). This replaces Alembic: the migrations are hand-written SQL
(row-level security and `SECURITY DEFINER` functions are awkward to express through an ORM), so there
is no new dependency.

## Rolling back a release
`helm rollback tycheon -n tycheon --wait` restores the previous Deployments. It does not undo the
schema; migrations are written to be additive so the previous release keeps working.
