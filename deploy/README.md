# Deploy

| Path | Status | Contents |
|---|---|---|
| `docker/` | shipped | Dev-stack support files (Postgres init SQL). The dev stack itself is `docker-compose.dev.yml` at the repo root. |
| `helm/` | planned | Chart for the OSS forecast/risk server and the Cloud control plane. |
| `terraform/` | planned | AWS-first infrastructure modules. |

Nothing here is production-ready yet. The dev stack binds every service to
`localhost` with placeholder credentials and must not be exposed. Market data
is licensed per customer; a deployment never ships exchange data with it.
