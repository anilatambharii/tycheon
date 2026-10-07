# Tycheon infrastructure (Terraform)

Reusable modules that provision everything the Tycheon Helm chart needs on AWS or GCP.

| Path | What |
|------|------|
| `aws/` | Module: VPC, EKS (CPU + GPU pools), IRSA roles, RDS PostgreSQL 16, ElastiCache Redis, S3, KMS, WAFv2, security groups, flow logs |
| `aws/examples/complete/` | `profile = "saas"` (Tycheon Cloud), creates its own VPC |
| `aws/examples/customer-vpc/` | `profile = "customer_vpc"`, existing VPC + subnets, WAF off |
| `gcp/` | Smaller sibling module: VPC, GKE (CPU + GPU pools), Cloud SQL, Memorystore, GCS, Cloud KMS, LB prerequisites + Cloud Armor, workload identity |
| `gcp/examples/complete/` | Full GCP example |
| `verify.sh` | fmt / init / validate / offline plan for every module and example, prints a pass/fail table |

The modules do **not** configure providers; the examples do. Each root and example commits its
`.terraform.lock.hcl` (provider hashes only, for linux/darwin/windows).

> Cost warning: these stacks create NAT gateways (about 3 x $33/month plus data processing), a Multi-AZ
> `db.r6g.xlarge`, a 2-node `cache.r6g.large`, an EKS control plane ($73/month), at least three
> `m6i.2xlarge` nodes, WAF, KMS keys and log storage. Expect roughly low-to-mid four figures USD per
> month for the `saas` profile **before** any GPU use. The GPU pool defaults to 0 nodes, but a single
> `g5.2xlarge` is about $1.2/hour while running. On GCP, expect a comparable order of magnitude (regional GKE,
> HA Cloud SQL, HA Memorystore, Cloud NAT). Always `terraform plan` and check the bill estimate; destroy
> throwaway environments (set `db_deletion_protection = false` / `deletion_protection = false` first).

## Quick start (AWS)

```bash
cd aws/examples/complete
cp terraform.tfvars.example terraform.tfvars    # edit account_id, region, AZs, admin role ARNs
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars      # real account only; creates billable resources
terraform output -json helm_values              # feed to the Helm chart
```

Ingress wiring (the ALB is created by the AWS Load Balancer Controller, not by Terraform):

```yaml
alb.ingress.kubernetes.io/security-groups: <alb_security_group_id>
alb.ingress.kubernetes.io/manage-backend-security-group-rules: "false"
alb.ingress.kubernetes.io/wafv2-acl-arn: <waf_web_acl_arn>      # omit when enable_waf = false
```

Install the controller with `clusterName`, `region` and `vpcId` set from the outputs (nodes enforce IMDSv2 with hop
limit 1, so pods cannot auto-discover them) and `serviceAccount.annotations."eks.amazonaws.com/role-arn" = role_arns["lb_controller"]`.
For GPU scale-from-zero install the Cluster Autoscaler with `role_arns["cluster_autoscaler"]` and the NVIDIA device plugin.
Schedule GPU work with `nodeSelector: tycheon.io/pool=gpu` and a toleration for `nvidia.com/gpu=true:NoSchedule`.

## Quick start (GCP)

```bash
cd gcp/examples/complete
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform plan -var-file=terraform.tfvars
```

First apply on a fresh project: API enablement and Google-managed service agents are created in the same run
(`google_project_service`, `google_project_service_identity`) and CMEK grants depend on them, but propagation lag is real. If the
first apply fails on a KMS binding or CMEK-protected resource, re-run `apply`. The `google-beta` provider is required only for
`google_project_service_identity`.

## Profiles (AWS)

Set with `profile`; every default below can be overridden by the specific variable.

| | `saas` | `customer_vpc` |
|---|---|---|
| VPC | Created (3 AZ, public + private subnets, NAT per AZ, S3 gateway endpoint) | **Existing**: `existing_vpc_id` + `existing_private_subnet_ids` (>= 2) required, nothing network-level is created |
| NAT | One per AZ (`single_nat_gateway = false`) | n/a |
| RDS | `db.r6g.xlarge`, 200 GiB, **Multi-AZ**, **deletion protection**, 35-day backups | `db.m6g.large`, 100 GiB, single-AZ, deletion protection on, 14-day backups |
| Redis | `cache.r6g.large` x2 (failover + Multi-AZ) | `cache.m6g.large` x1 |
| CPU pool | `m6i.2xlarge`, 3-12 | `m6i.xlarge`, 2-6 |
| WAF | `enable_waf = true` | typically `enable_waf = false` (customer fronts the ALB) |
| ALB ingress | `0.0.0.0/0:443` by default (restrict with `alb_ingress_cidrs`) | set to the customer networks |

For `customer_vpc` the customer must tag subnets for the AWS Load Balancer Controller
(`kubernetes.io/role/elb=1` public, `kubernetes.io/role/internal-elb=1` private) and provide private egress (NAT or VPC endpoints for ECR, S3, STS, etc.).
Flow logs attach to the existing VPC unless `enable_flow_logs = false`.

## AWS module variables (main ones)

All variables are validated; see `aws/variables.tf` for the complete list and error messages.

| Variable | Default | Validation / notes |
|---|---|---|
| `profile` | `saas` | `saas` or `customer_vpc` |
| `name`, `environment` | `tycheon`, (required) | lowercase, <= 12 / <= 8 chars (bucket names embed them) |
| `account_id` | (required) | 12 digits. Passed in so plan makes no `sts:GetCallerIdentity` call |
| `availability_zones` | `[]` | >= 3 required when the module creates the VPC |
| `tags` | `{}` | Merged over standard tags (`Application`, `Environment`, `Profile`, `ManagedBy`) |
| `vpc_cidr` | `10.40.0.0/16` | valid CIDR, /16-/18 |
| `existing_vpc_id`, `existing_private_subnet_ids` | `null`, `[]` | `vpc-...` / `subnet-...` format; >= 2 subnets |
| `single_nat_gateway` | profile | |
| `enable_flow_logs`, `log_retention_days` | `true`, `90` | valid CloudWatch retention, >= 7 |
| `alb_ingress_cidrs` | `["0.0.0.0/0"]` | non-empty valid CIDRs; the only world-open ingress |
| `kubernetes_version` | `1.33` | `1.NN` |
| `endpoint_public_access`, `public_access_cidrs` | `false`, `[]` | `0.0.0.0/0` rejected when public |
| `eks_admin_role_arns` | `[]` | IAM role/user ARNs, granted cluster-admin via access entries |
| `cpu_node_group` | profile | non-empty instance types, `min_size >= 1`, ON_DEMAND/SPOT |
| `gpu_node_group` | `g5.2xlarge`, 0/0/4, `AL2023_x86_64_NVIDIA` | `0 <= min <= desired <= max`, NVIDIA AMI types only; `enabled = false` removes the pool |
| `db_instance_class`, `db_allocated_storage_gb` | profile | `db.x.y` format; >= 20 GiB |
| `db_multi_az`, `db_deletion_protection` | profile | |
| `db_backup_retention_days` | profile (35 / 14) | **7-35** |
| `db_performance_insights_retention_days` | `7` | 7, 731 or multiple of 31 |
| `redis_node_type`, `redis_num_cache_clusters` | profile | `cache.x.y`; 1-6 |
| `redis_snapshot_retention_days` | `7` | 1-35 |
| `s3_noncurrent_version_expiration_days` | `90` | >= 7 |
| `kms_deletion_window_days` | `30` | 7-30 |
| `app_kms_key_spec` | `SYMMETRIC_DEFAULT` | `RSA_*` allowed but KMS cannot auto-rotate asymmetric keys |
| `enable_waf`, `waf_rate_limit` | `true`, `2000` | rate >= 10 |
| `waf_managed_rule_groups` | Common, KnownBadInputs, IpReputation, SQLi | `AWSManagedRules...` names |
| `waf_common_ruleset_count_only_rules` | `["SizeRestrictions_BODY"]` | see "Known trade-offs" |

Outputs (all non-secret): `cluster_name`, `cluster_endpoint`, `oidc_provider_arn`, `oidc_issuer_url`, `role_arns`
(api, control_plane, worker, lb_controller, ebs_csi, vpc_cni, cluster_autoscaler), `db_endpoint`, `db_master_secret_arn`,
`redis_primary_endpoint`, `redis_auth_secret_arn`, `bucket_names`, `kms_key_arns`, `alb_security_group_id`,
`waf_web_acl_arn`, `vpc_id`, `region`, and a ready-made `helm_values` map.

## GCP module variables (main ones)

| Variable | Default | Notes |
|---|---|---|
| `project_id`, `project_number`, `region` | (required) | project number is a variable so plan needs no lookup |
| `name`, `environment` | `tycheon`, (required) | same limits as AWS |
| `labels` | `{}` | GCP label syntax validated; merged over `application/environment/managed_by` |
| `deletion_protection` | `true` | GKE cluster + Cloud SQL |
| `master_authorized_networks` | (required) | non-empty, `0.0.0.0/0` rejected |
| `enable_private_endpoint` | `true` | |
| `cpu_node_pool` / `gpu_node_pool` | `n2-standard-8` 1-12 / `g2-standard-8` + `nvidia-l4` 0-4 | GPU pool tainted `nvidia.com/gpu=true:NoSchedule`, label `tycheon.io/pool=gpu` |
| `db_tier`, `db_availability_type`, `db_backup_retention_count` | `db-custom-4-16384`, `REGIONAL`, `14` | retention >= 7, PITR always on |
| `redis_tier`, `redis_memory_size_gb`, `redis_version` | `STANDARD_HA`, `5`, `REDIS_7_2` | TLS + AUTH always on |
| `kms_rotation_period_days`, `kms_protection_level` | `90`, `SOFTWARE` | |
| `enable_lb_resources`, `lb_domains` | `true`, (required if enabled) | static IP, managed cert, SSL policy, Cloud Armor |
| `armor_rate_limit_per_minute`, `armor_waf_rules` | `600`, six preconfigured rule sets | |

The GCP module intentionally has one profile (SaaS-style, secure defaults) and does not support an existing VPC.

## State backend (example only; not enabled)

State holds generated secrets (see "Security notes"), so use an encrypted, access-controlled, versioned backend.

```hcl
# AWS: S3 + DynamoDB lock (use_lockfile = true is the newer S3-native alternative to DynamoDB)
# terraform {
#   backend "s3" {
#     bucket         = "my-org-tfstate"            # versioned, SSE-KMS, public access blocked
#     key            = "tycheon/prod/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "my-org-tfstate-lock"       # partition key: LockID (string)
#     encrypt        = true
#     kms_key_id     = "alias/my-org-tfstate"
#   }
# }

# GCP: GCS (native locking)
# terraform {
#   backend "gcs" {
#     bucket = "my-org-tfstate"                    # versioning on, CMEK, uniform access
#     prefix = "tycheon/prod"
#   }
# }
```

## Security posture

* No public database or cache: RDS / ElastiCache / Cloud SQL / Memorystore are private-only, and their security groups accept only the cluster security group (AWS) or private service access (GCP).
* The only world-open ingress is the ALB on 443 (AWS, `alb_ingress_cidrs`, narrowable). Route-table `0.0.0.0/0` default routes exist only for IGW/NAT egress. Endpoint access to the Kubernetes API is private by default; public access is rejected unless restricted to non-`0.0.0.0/0` CIDRs.
* Encryption: KMS CMK for RDS, Performance Insights, RDS master secret, Redis (at rest + in transit, AUTH), S3 (SSE-KMS + bucket keys), EKS secrets envelope, CloudWatch Logs, Secrets Manager. EBS node volumes are encrypted with the account default EBS key (a CMK on node volumes needs an account-specific Auto Scaling grant; deliberately left out). GCP: CMEK for Cloud SQL, Memorystore, GCS, GKE secrets, Secret Manager; node boot disks use Google-managed keys.
* Key rotation on: the AWS data key and the (default symmetric) app key. Asymmetric KMS keys cannot be auto-rotated by AWS. GCP keys rotate every `kms_rotation_period_days`.
* Wildcard IAM, each deliberate:
  * AWS Load Balancer Controller policy (`aws/policies/aws-load-balancer-controller.json`): upstream policy, `Resource: "*"` for `Describe*`, SG authorize/revoke and the ELB mutation calls (which AWS cannot scope by ARN before the resources exist; mutations are constrained by `elbv2.k8s.aws/cluster` tag conditions).
  * Cluster Autoscaler: `Describe*` on `*` (no resource-level support); scale actions only on ASGs tagged as owned by this cluster.
  * KMS key policies: `kms:*` for the account root on `*` (means "this key"; standard way to enable IAM-governed access); CloudWatch Logs service statement scoped by encryption context.
  * Managed policies `AmazonEKS_CNI_Policy`, `AmazonEBSCSIDriverPolicy`, `AmazonEKSClusterPolicy`, `AmazonEKSWorkerNodePolicy`, `AmazonEC2ContainerRegistryPullOnly`, `AmazonRDSEnhancedMonitoringRole` are AWS-owned.
  * GCP: node service account roles (`logging.logWriter`, `monitoring.*`, `artifactregistry.reader`) are project-level because those APIs have no narrower scope.
* Workload roles (api / control-plane / worker): S3 limited to bucket + prefix, KMS data-key use restricted with `kms:ViaService`, Secrets Manager restricted to the two secret ARNs, `rds-db:connect` to one DB user. GCP equivalents use bucket IAM with prefix conditions, per-secret accessor, key-level grants and a Cloud SQL instance condition.
* No hard-coded account IDs: `account_id` / `project_number` are inputs (the example tfvars use the AWS documentation placeholder `123456789012`).
* Secrets: the RDS master password is generated and rotated by RDS (`manage_master_user_password`). The Redis AUTH token (AWS) and Cloud SQL `postgres` password (GCP) are generated by Terraform (`random_password`) and stored in Secrets Manager / Secret Manager. They are never variables, defaults or outputs, but **they do exist in Terraform state** (Memorystore also returns its AUTH string into state). Protect the state backend accordingly, or rotate after bootstrap.

## Known trade-offs and caveats

* WAF `AWSManagedRulesCommonRuleSet` `SizeRestrictions_BODY` is set to Count by default because its 8 KB body limit blocks normal time-series payloads. Review WAF logs and tighten (`waf_common_ruleset_count_only_rules = []`) if your payloads are small.
* The ALB / GCP load balancer are created by the in-cluster controllers from Ingress objects, so Terraform cannot attach WAF / Cloud Armor directly; it outputs the IDs/names for the annotations.
* Node IMDS hop limit is 1 (pods cannot read node credentials). Controllers that auto-discover region/VPC through IMDS need explicit values.
* `desired_size` of node groups is ignored after creation (autoscaler owns it).
* KMS key rings/keys on GCP can never be deleted; AWS keys have a deletion window.
* The load-balancer-controller policy was written by hand, then compared with the upstream `iam_policy.json` of controller release v3.6.0: the set of 80 allowed actions is identical. Conditions and resource scoping were not diffed; compare them against the release you deploy before production use.

## What was and was not verified

Verified (Terraform 1.16.5, Linux/WSL, AWS provider 6.67.0, `random` 3.9.1, run by `./verify.sh` with no real credentials):

| Target | fmt | init | validate | offline plan |
|---|---|---|---|---|
| `aws` (module) | pass | pass | pass | n/a (module needs inputs) |
| `aws/examples/complete` | pass | pass | pass | pass: `Plan: 108 to add, 0 to change, 0 to destroy.` |
| `aws/examples/customer-vpc` | pass | pass | pass | pass: `Plan: 75 to add, 0 to change, 0 to destroy.` |
| `gcp` (module) | pass | pass | pass | n/a |
| `gcp/examples/complete` | pass | pass | pass | pass: `Plan: 87 to add, 0 to change, 0 to destroy.` |

The offline plans use `-refresh=false -lock=false -var-file=terraform.tfvars.example`, dummy AWS keys plus the provider
`skip_*` flags (AWS, via `-var offline_validation=true`), and `GOOGLE_OAUTH_ACCESS_TOKEN=dummy-token` for GCP (a pre-supplied token stops the
google provider searching for Application Default Credentials). Variable validations and preconditions were also exercised with bad inputs and rejected them.

NOT verified (needs a real account, none was used):

* `terraform apply` / `destroy` has never been run. Provider-side validation that only happens at apply (name collisions, quota, unsupported engine minor versions, KMS grant propagation, IAM policy size/syntax as accepted by AWS, WAF rule schema, Cloud Armor expression acceptance) is untested.
* Real behaviour of ALB + WAF / Cloud Armor attachment, the AWS Load Balancer Controller policy, IRSA / Workload Identity token exchange, and RDS/Redis connectivity from pods.
* GPU: availability of `AL2023_x86_64_NVIDIA` for the chosen Kubernetes version, instance/accelerator availability per AZ/zone, GPU quotas, and scale-from-zero via the autoscaler.
* Cost figures above are rough, unmeasured estimates.
* No static analysis beyond `terraform validate` (tflint, tfsec/checkov were not available).
* Several data sources are deferred to apply (`aws_iam_policy_document` over not-yet-known ARNs); they are local computations but were not exercised against real values.
