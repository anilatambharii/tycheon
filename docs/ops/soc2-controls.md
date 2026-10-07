# SOC 2 aligned control documentation

> **This is not a SOC 2 report and Tycheon is not SOC 2 certified.** It maps the controls the
> product and its operations already have to the AICPA Trust Services Criteria (Security, plus
> Availability and Confidentiality), points at the evidence in this repository, and says where a
> control is missing. An auditor needs a period of operating evidence, policies adopted by the
> organisation, and a real production environment; none of that exists yet.

Status key: **Built** (implemented and tested in this repository), **Designed** (documented, not yet
operating), **Gap** (not done).

## Common criteria (Security)

| Criterion | Control | Status | Evidence |
|---|---|---|---|
| CC1 Control environment | A code of conduct, contribution rules and a security policy | Built | `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md` |
| CC1 | Roles and responsibilities for operations and incidents | Designed | [incident response](incident-response.md) |
| CC2 Communication | Customer-facing disclaimers and honest limits; a documented incident communication process | Built / Designed | [safety](../safety.md), [incident response](incident-response.md) |
| CC3 Risk assessment | Threat-informed design decisions recorded as ADRs; a known-risks list at launch | Built | `docs/adr/`, the launch checklist |
| CC4 Monitoring | SLOs, alerts and runbooks; CI on every change | Built (rules validated, not run on production) | [SLOs](slos.md), `prometheusrule.yaml` |
| CC5 Control activities | Branch/PR workflow with required checks; CODEOWNERS | **Gap**: `main` is not protected and there is no CODEOWNERS file (found by the Scorecard audit) | `docs/launch/scorecard-audit.md` |
| CC6.1 Logical access | Per-tenant isolation enforced by the database (row-level security), tested as the unprivileged role; least-privilege roles; no BYPASSRLS | Built | `ee/tests/test_rls_isolation.py`, ADR 0009 |
| CC6.1 | Authentication: scrypt password hashes, API keys stored as hashes, short-lived signed sessions, OIDC SSO with PKCE and strict token validation | Built | `ee/tests/test_security_crypto_plans.py`, `test_sso.py` |
| CC6.1 | Agents act only through capability grants, policy and audit (Keelgate); paper trading only, never auto-approved | Built | ADR 0007, `tests/test_governance.py` |
| CC6.2-6.3 | Provisioning and removal of access: API keys are created and revoked by the owner; roles viewer/member/admin/owner; operator access separate and token-gated | Built | `ee/tests/test_api.py`, `test_billing.py` |
| CC6.1 | Production access for staff (cloud console, cluster, database) | **Gap**: not defined; needs SSO + MFA + least privilege + access reviews | n/a |
| CC6.6 | Boundary protection: network policies default-deny, private database and cache, WAF, TLS-only buckets | Designed (Terraform plans offline; never applied) | `deploy/terraform`, `deploy/helm` |
| CC6.7 | Data in transit encrypted (TLS at the load balancer, Redis TLS, `sslmode` to Postgres); at rest (KMS-encrypted RDS, S3, EBS defaults) | Designed | `deploy/terraform` |
| CC6.8 | Malware / supply chain: pinned base images by digest, SBOM per image, vulnerability scanning that fails the build on fixable HIGH/CRITICAL, keyless signing and verification, dependency review | Built in CI (not yet run on GitHub) | `images.yml`, `Dockerfile` |
| CC7.1-7.2 | Detection: metrics and alerts; audit log (hash-chained for agents, append-only for management actions); secret scanning (gitleaks, detect-secrets) | Built | `metrics.py`, `.github/workflows/secret-scan.yml` |
| CC7.3-7.5 | Incident response and recovery | Designed | [incident response](incident-response.md), [backup and restore](backup-restore.md) (logical drill passed) |
| CC8.1 Change management | Every change through a pull request with lint, types, tests, scans; migrations with tested rollbacks; atomic Helm deploys; manual approval for production | Built (CI), Designed (production approval) | `ci.yml`, `images.yml`, `ee/tests/test_migrations.py` |
| CC9 Risk mitigation / vendors | Vendor list and review: Stripe, cloud provider, Hugging Face (weights pinned by commit, safetensors only), GitHub | **Gap**: no vendor review process | n/a |

## Availability

| Criterion | Control | Status | Evidence |
|---|---|---|---|
| A1.1 Capacity | HPAs, PodDisruptionBudgets, GPU node group that scales from zero, load-bearing limits per plan | Designed | chart, Terraform |
| A1.2 Backup and recovery | Automated snapshots + a manual snapshot before every production deploy; tested logical restore | Built (drill), Designed (RDS) | `deploy/ops/backup_restore_test.py` |
| A1.3 Recovery testing | Quarterly restore drill | **Gap**: scheduled, not yet run on real infrastructure | [backup and restore](backup-restore.md) |

## Confidentiality

| Criterion | Control | Status | Evidence |
|---|---|---|---|
| C1.1 Identify and protect | Customer data is stored per tenant and never shared; licensed market data is never redistributed (customers bring their own); credentials envelope-encrypted | Built | `docs/cloud.md`, `ee/tests/test_api.py` |
| C1.1 | No tenant identifiers in metrics, traces scrubbed of credentials | Built | `test_metrics.py`, the collector's `attributes/scrub` processor |
| C1.2 Disposal | Per-plan retention deletes expired operational data; deleting a data source deletes its files | Built | `retention.py`, `ee/tests/test_retention_cli.py` |
| C1.2 | Deleting an organisation and all its data on request | **Gap**: no endpoint or runbook yet | n/a |

## Other things an auditor will ask for that do not exist
Written and adopted policies (information security, access control, acceptable use, vendor
management, business continuity, HR/onboarding), security awareness training, background checks,
penetration testing by a third party, a risk register with owners, a data-processing agreement
and privacy notice, and months of operating evidence for each control. Plan for a readiness
assessment before choosing a Type I or Type II audit.
