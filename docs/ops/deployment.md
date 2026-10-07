# Deployment

## What gets deployed

| Component | Image | Notes |
|---|---|---|
| Open-source API | `tycheon-api` | `tycheon-serve`: forecast, calibrate, risk, report, backtest, approvals |
| Control plane | `tycheon-cloud` | orgs, keys, SSO, metering, billing, governed analytics |
| MCP endpoint | `tycheon-cloud` (separate Deployment) | the control plane's `/v1/mcp`, scaled and rate-limited on its own |
| CPU workers | `tycheon-cloud` | `python -m tycheon_ft.worker --device cpu` |
| GPU workers | `tycheon-cloud` `-cuda` | same worker, GPU PyTorch, GPU node pool + toleration |
| Dashboard | `tycheon-dashboard` | Next.js, talks to the control plane through its own server |
| Telemetry | upstream OpenTelemetry collector | scrubs credentials from spans, exposes Prometheus metrics |

All Tycheon images run as an unprivileged user (uid 10001) with a read-only root filesystem, no
capabilities and no setuid binaries. `deploy/docker/verify_image.sh` checks this on a running container;
CI runs it on every change. Base images are pinned by digest.

MCP: the open-source package serves MCP over **stdio only** (a client launches it); there is no
network MCP server in the OSS image. The network endpoint is the Cloud control plane's `/v1/mcp`,
authenticated per API key with the `mcp` scope and gated by plan.

## Images

```
docker build --target api   -t tycheon-api:dev   .                         # CPU
docker build --target cloud -t tycheon-cloud:dev .                         # CPU
docker build --target cloud --build-arg TORCH_BACKEND=cu124 -t tycheon-cloud:dev-cuda .   # GPU (large)
docker build -t tycheon-dashboard:dev ee/dashboard
```

The CUDA variant bundles the CUDA runtime in the PyTorch wheels, so the host needs only an NVIDIA
driver and the NVIDIA container runtime / device plugin. The CPU images are about 2.6 GB (PyTorch is
most of it); the dashboard is about 440 MB. Smaller images are possible by dropping the `kronos`
extra from deployments that never run neural models. The proprietary images are private: pull them
with an image pull secret.

## Helm

`deploy/helm/tycheon`, with one values file per profile:

| Profile | File | For |
|---|---|---|
| defaults | `values.yaml` | only the open-source API; nothing exposed; no secrets in the file |
| SaaS | `values-saas.yaml` + `environments/{staging,production}.yaml` | Tycheon-operated: control plane, MCP, CPU and GPU workers, dashboard, ALB ingress, network policies, collector and alert rules |
| Customer VPC | `values-customer-vpc.yaml` | private deployment: internal load balancer, no egress to the internet, no Stripe, pre-seeded model cache |
| kind | `values-kind.yaml` | CPU smoke test with a throwaway in-cluster Postgres (never for production) |

What the chart gives every workload: non-root, read-only filesystem, dropped capabilities,
`RuntimeDefault` seccomp, no service-account token mounted, resource requests and limits, startup,
readiness and liveness probes, topology spread across zones, rolling updates with no unavailable pods,
HPAs (CPU) and PodDisruptionBudgets for the stateless services, a default-deny `NetworkPolicy` with
only the needed holes (ingress controller to the services, pods to each other, DNS, the database and
cache CIDRs, and optionally HTTPS out), a model-cache volume filled once by a pinned-revision
prefetch Job, and a pre-upgrade migration Job.

Secrets are **never** in the chart: workloads read Kubernetes Secrets you create or sync
(`tycheon-api-keys`, `tycheon-cloud`, `tycheon-migrations`; the keys are listed in `values.yaml`).

Worker scaling is by replica count; scaling on queue depth (KEDA) is future work: the queue length is
exported (`tycheon_finetune_jobs_queued`) so it can be added without code changes.

## Terraform

`deploy/terraform/aws` is the module (VPC, EKS with a GPU node group that scales from zero, RDS
PostgreSQL 16, ElastiCache Redis, S3, KMS, ALB prerequisites and WAF, IRSA roles) with `examples/complete`
(SaaS) and `examples/customer-vpc`; `deploy/terraform/gcp` is the second module (GKE, Cloud SQL,
Memorystore, GCS, Cloud KMS, Cloud Armor, workload identity). `deploy/terraform/verify.sh` formats,
validates and plans every example offline. **No `terraform apply` has ever been run**; read the
module README's "what was and was not verified" before the first apply.

## Customer-VPC differences
* the chart pulls the proprietary images from a private registry (`global.imagePullSecrets`);
* the ingress is internal; no Stripe or other outbound calls are made unless you open the egress;
* the model cache is pre-seeded rather than downloaded (the cluster has no route to Hugging Face);
* telemetry stays inside the customer's network (set `otel.exporters.otlpEndpoint` only if wanted);
* the customer holds the KMS key and the database credentials.
