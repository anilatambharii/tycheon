# Operations

How Tycheon is built, shipped and run in production. For research and risk analytics. Not investment advice.

| | |
|---|---|
| [Deployment](deployment.md) | images, the Helm chart, Terraform, SaaS vs customer-VPC |
| [CI/CD](cicd.md) | build, scan, sign, push; staging on `main`; approval to production; migrations and rollback |
| [SLOs and alerts](slos.md) | what we promise, how it is measured, what pages someone |
| [Backup and restore](backup-restore.md) | what is backed up, the restore procedure, the drill that tests it |
| [Incident response](incident-response.md) | roles, severities, the first hour, security incidents, reviews |
| [Runbooks](runbooks/error-budget-burn.md) | one per alert family |
| [SOC 2 aligned controls](soc2-controls.md) | control mapping, evidence, and the gaps (this is **not** a SOC 2 report) |

Everything in these pages is stated as verified, designed, or a gap. The launch checklist
([`docs/launch/launch-checklist.md`](https://github.com/anilatambharii/tycheon/blob/main/docs/launch/launch-checklist.md)) lists what has actually run and what has not.
