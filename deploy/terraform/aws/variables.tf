# -----------------------------------------------------------------------------
# Identity / profile
# -----------------------------------------------------------------------------

variable "profile" {
  description = <<-EOT
    Deployment profile.
      saas         - Tycheon Cloud: creates its own VPC, multi-AZ data stores, deletion protection, larger sizes, WAF.
      customer_vpc - Customer-hosted: deploys into an existing VPC (existing_vpc_id + existing_private_subnet_ids),
                     smaller defaults; set enable_waf = false if the customer fronts the ALB themselves.
    Every profile default can be overridden by the explicit variable it feeds.
  EOT
  type        = string
  default     = "saas"

  validation {
    condition     = contains(["saas", "customer_vpc"], var.profile)
    error_message = "profile must be \"saas\" or \"customer_vpc\"."
  }
}

variable "name" {
  description = "Short name prefix for all resources (lowercase, max 12 chars)."
  type        = string
  default     = "tycheon"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,11}$", var.name))
    error_message = "name must be 2-12 chars: lowercase letters, digits, hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name, e.g. prod, staging (lowercase alphanumerics, max 8 chars)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.environment))
    error_message = "environment must be 2-8 chars: lowercase letters and digits, starting with a letter."
  }
}

variable "account_id" {
  description = "AWS account ID the stack is deployed into. Passed in (not looked up) so that plan needs no AWS API call; used in KMS key policies and bucket names."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12 digit AWS account ID."
  }
}

variable "availability_zones" {
  description = "AZ names to spread subnets over. Required (>= 3) when the module creates the VPC; ignored for an existing VPC. Passed in so that plan needs no AWS API call."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.availability_zones) <= 6 && length(distinct(var.availability_zones)) == length(var.availability_zones)
    error_message = "availability_zones must contain at most 6 distinct names."
  }
}

variable "tags" {
  description = "Extra tags applied to every resource (merged over the module's standard tags)."
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# Network
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR of the VPC created when existing_vpc_id is null. Must be between /16 and /18."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 18
    error_message = "vpc_cidr must be a valid CIDR with a prefix length between 16 and 18."
  }
}

variable "existing_vpc_id" {
  description = "Use an existing VPC instead of creating one (required for profile = customer_vpc)."
  type        = string
  default     = null

  validation {
    condition     = var.existing_vpc_id == null || can(regex("^vpc-[0-9a-f]{8,17}$", coalesce(var.existing_vpc_id, "x")))
    error_message = "existing_vpc_id must look like vpc-0123456789abcdef0."
  }
}

variable "existing_private_subnet_ids" {
  description = "Private subnet IDs (>= 2 AZs, with egress via NAT/endpoints) for EKS nodes, RDS and Redis when using an existing VPC. For the AWS Load Balancer Controller the customer must tag public subnets kubernetes.io/role/elb=1 and these kubernetes.io/role/internal-elb=1."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.existing_private_subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", s))])
    error_message = "Every entry of existing_private_subnet_ids must look like subnet-0123456789abcdef0."
  }
}

variable "single_nat_gateway" {
  description = "Use one shared NAT gateway instead of one per AZ (cheaper, not AZ-resilient). null = profile default (saas: false)."
  type        = bool
  default     = null
}

variable "enable_flow_logs" {
  description = "Enable VPC flow logs to a KMS-encrypted CloudWatch log group."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retention (days) for flow logs, EKS control-plane logs and WAF logs. Must be a valid CloudWatch retention value >= 7."
  type        = number
  default     = 90

  validation {
    condition     = contains([7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a valid CloudWatch Logs retention value that is >= 7."
  }
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB on 443. The ALB is the ONLY thing in this stack that may accept 0.0.0.0/0. Restrict this for private/customer deployments."
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.alb_ingress_cidrs) > 0 && alltrue([for c in var.alb_ingress_cidrs : can(cidrhost(c, 0))])
    error_message = "alb_ingress_cidrs must be a non-empty list of valid CIDRs."
  }
}

variable "app_port" {
  description = "Container port the ALB forwards to (used for the ALB -> node security group rule)."
  type        = number
  default     = 8080

  validation {
    condition     = var.app_port >= 1024 && var.app_port <= 65535
    error_message = "app_port must be between 1024 and 65535."
  }
}

# -----------------------------------------------------------------------------
# EKS
# -----------------------------------------------------------------------------

variable "kubernetes_version" {
  description = "EKS Kubernetes minor version."
  type        = string
  default     = "1.33"

  validation {
    condition     = can(regex("^1\\.[0-9]{2}$", var.kubernetes_version))
    error_message = "kubernetes_version must look like 1.33."
  }
}

variable "endpoint_public_access" {
  description = "Expose the Kubernetes API endpoint publicly (restricted to public_access_cidrs). Default is private-only; reach it from the VPC, VPN or a bastion."
  type        = bool
  default     = false
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public API endpoint when endpoint_public_access = true. 0.0.0.0/0 is rejected."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.public_access_cidrs : can(cidrhost(c, 0))])
    error_message = "public_access_cidrs must contain valid CIDRs."
  }
}

variable "cluster_log_types" {
  description = "EKS control-plane log types to enable."
  type        = list(string)
  default     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  validation {
    condition     = alltrue([for t in var.cluster_log_types : contains(["api", "audit", "authenticator", "controllerManager", "scheduler"], t)])
    error_message = "cluster_log_types may only contain api, audit, authenticator, controllerManager, scheduler."
  }
}

variable "eks_admin_role_arns" {
  description = "IAM role/user ARNs granted cluster-admin through EKS access entries (in addition to the identity that creates the cluster)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for a in var.eks_admin_role_arns : can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:(role|user)/.+$", a))])
    error_message = "eks_admin_role_arns must be IAM role or user ARNs."
  }
}

variable "cpu_node_group" {
  description = "CPU managed node group. Any null field takes the profile default."
  type = object({
    instance_types = optional(list(string))
    min_size       = optional(number)
    max_size       = optional(number)
    desired_size   = optional(number)
    capacity_type  = optional(string, "ON_DEMAND")
    disk_size_gb   = optional(number, 100)
    ami_type       = optional(string, "AL2023_x86_64_STANDARD")
  })
  default = {}

  validation {
    condition     = var.cpu_node_group.instance_types == null || (length(var.cpu_node_group.instance_types) > 0 && alltrue([for t in var.cpu_node_group.instance_types : length(trimspace(t)) > 0]))
    error_message = "cpu_node_group.instance_types must be a non-empty list of non-empty instance types."
  }
  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.cpu_node_group.capacity_type)
    error_message = "cpu_node_group.capacity_type must be ON_DEMAND or SPOT."
  }
  validation {
    condition     = var.cpu_node_group.disk_size_gb >= 20 && var.cpu_node_group.disk_size_gb <= 16000
    error_message = "cpu_node_group.disk_size_gb must be between 20 and 16000."
  }
  validation {
    condition     = (var.cpu_node_group.min_size == null || var.cpu_node_group.min_size >= 1) && (var.cpu_node_group.max_size == null || var.cpu_node_group.max_size >= 1)
    error_message = "The CPU pool hosts system pods and must keep min_size >= 1."
  }
}

variable "gpu_node_group" {
  description = "GPU managed node group (tainted nvidia.com/gpu=true:NoSchedule, labelled tycheon.io/pool=gpu). Defaults to scale-from-zero: min 0, desired 0; pair with Cluster Autoscaler (role provided) or Karpenter."
  type = object({
    enabled        = optional(bool, true)
    instance_types = optional(list(string), ["g5.2xlarge"])
    min_size       = optional(number, 0)
    max_size       = optional(number, 4)
    desired_size   = optional(number, 0)
    capacity_type  = optional(string, "ON_DEMAND")
    disk_size_gb   = optional(number, 200)
    ami_type       = optional(string, "AL2023_x86_64_NVIDIA")
  })
  default = {}

  validation {
    condition     = length(var.gpu_node_group.instance_types) > 0 && alltrue([for t in var.gpu_node_group.instance_types : length(trimspace(t)) > 0])
    error_message = "gpu_node_group.instance_types must be a non-empty list of non-empty instance types."
  }
  validation {
    condition     = var.gpu_node_group.min_size >= 0 && var.gpu_node_group.max_size >= 1 && var.gpu_node_group.min_size <= var.gpu_node_group.desired_size && var.gpu_node_group.desired_size <= var.gpu_node_group.max_size
    error_message = "gpu_node_group requires 0 <= min_size <= desired_size <= max_size and max_size >= 1."
  }
  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.gpu_node_group.capacity_type)
    error_message = "gpu_node_group.capacity_type must be ON_DEMAND or SPOT."
  }
  validation {
    condition     = contains(["AL2023_x86_64_NVIDIA", "AL2023_ARM_64_NVIDIA", "BOTTLEROCKET_x86_64_NVIDIA", "BOTTLEROCKET_ARM_64_NVIDIA"], var.gpu_node_group.ami_type)
    error_message = "gpu_node_group.ami_type must be an NVIDIA AMI type (AL2023_x86_64_NVIDIA, AL2023_ARM_64_NVIDIA, BOTTLEROCKET_x86_64_NVIDIA, BOTTLEROCKET_ARM_64_NVIDIA)."
  }
  validation {
    condition     = var.gpu_node_group.disk_size_gb >= 20 && var.gpu_node_group.disk_size_gb <= 16000
    error_message = "gpu_node_group.disk_size_gb must be between 20 and 16000."
  }
}

variable "enable_cluster_autoscaler_role" {
  description = "Create the IRSA role for the Kubernetes Cluster Autoscaler (needed for GPU scale-from-zero)."
  type        = bool
  default     = true
}

variable "k8s_namespace" {
  description = "Namespace the Tycheon Helm release is installed into (used in IRSA trust policies)."
  type        = string
  default     = "tycheon"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.k8s_namespace))
    error_message = "k8s_namespace must be a valid Kubernetes namespace name."
  }
}

variable "service_account_names" {
  description = "Kubernetes ServiceAccount names (in k8s_namespace) bound to the api / control-plane / worker IAM roles."
  type = object({
    api           = optional(string, "tycheon-api")
    control_plane = optional(string, "tycheon-control-plane")
    worker        = optional(string, "tycheon-worker")
  })
  default = {}
}

# -----------------------------------------------------------------------------
# RDS PostgreSQL
# -----------------------------------------------------------------------------

variable "db_engine_version" {
  description = "PostgreSQL 16 engine version (major \"16\" lets RDS pick the current default minor)."
  type        = string
  default     = "16"

  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.db_engine_version))
    error_message = "db_engine_version must be 16 or 16.x."
  }
}

variable "db_instance_class" {
  description = "RDS instance class. null = profile default."
  type        = string
  default     = null

  validation {
    condition     = var.db_instance_class == null || can(regex("^db\\.[a-z0-9]+\\.[a-z0-9]+$", coalesce(var.db_instance_class, "x")))
    error_message = "db_instance_class must look like db.r6g.xlarge."
  }
}

variable "db_allocated_storage_gb" {
  description = "Initial storage (GiB). null = profile default."
  type        = number
  default     = null

  validation {
    condition     = var.db_allocated_storage_gb == null || (coalesce(var.db_allocated_storage_gb, 100) >= 20 && coalesce(var.db_allocated_storage_gb, 100) <= 65536)
    error_message = "db_allocated_storage_gb must be between 20 and 65536."
  }
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling (GiB). null = 4x allocated storage."
  type        = number
  default     = null
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "tycheon"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,62}$", var.db_name))
    error_message = "db_name must be lowercase letters, digits and underscores, starting with a letter."
  }
}

variable "db_master_username" {
  description = "Master username. The password is generated and rotated by RDS in Secrets Manager (manage_master_user_password); it never appears in Terraform variables."
  type        = string
  default     = "tycheon_admin"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{2,31}$", var.db_master_username)) && !contains(["admin", "postgres", "rdsadmin"], var.db_master_username)
    error_message = "db_master_username must be 3-32 lowercase alphanumerics/underscores and not a reserved name."
  }
}

variable "db_iam_username" {
  description = "Database user the workloads connect as with IAM database authentication (rds-db:connect is granted for exactly this user)."
  type        = string
  default     = "tycheon_app"
}

variable "db_multi_az" {
  description = "Multi-AZ standby. null = profile default (saas: true)."
  type        = bool
  default     = null
}

variable "db_deletion_protection" {
  description = "Deletion protection. null = profile default (true for both profiles)."
  type        = bool
  default     = null
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. Leave false outside throwaway environments."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "Automated backup retention in days (7-35). null = profile default (saas: 35, customer_vpc: 14)."
  type        = number
  default     = null

  validation {
    condition     = var.db_backup_retention_days == null || (coalesce(var.db_backup_retention_days, 7) >= 7 && coalesce(var.db_backup_retention_days, 7) <= 35)
    error_message = "db_backup_retention_days must be between 7 and 35."
  }
}

variable "db_backup_window" {
  description = "Daily backup window (UTC) hh:mm-hh:mm."
  type        = string
  default     = "03:00-04:00"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]$", var.db_backup_window))
    error_message = "db_backup_window must look like 03:00-04:00."
  }
}

variable "db_maintenance_window" {
  description = "Weekly maintenance window (UTC) ddd:hh:mm-ddd:hh:mm."
  type        = string
  default     = "sun:05:00-sun:06:00"

  validation {
    condition     = can(regex("^(mon|tue|wed|thu|fri|sat|sun):([01][0-9]|2[0-3]):[0-5][0-9]-(mon|tue|wed|thu|fri|sat|sun):([01][0-9]|2[0-3]):[0-5][0-9]$", var.db_maintenance_window))
    error_message = "db_maintenance_window must look like sun:05:00-sun:06:00."
  }
}

variable "db_performance_insights_retention_days" {
  description = "Performance Insights retention: 7, 731, or a multiple of 31 up to 713."
  type        = number
  default     = 7

  validation {
    condition     = var.db_performance_insights_retention_days == 7 || var.db_performance_insights_retention_days == 731 || (var.db_performance_insights_retention_days % 31 == 0 && var.db_performance_insights_retention_days <= 713 && var.db_performance_insights_retention_days > 0)
    error_message = "db_performance_insights_retention_days must be 7, 731 or a multiple of 31 (<= 713)."
  }
}

variable "db_monitoring_interval" {
  description = "Enhanced Monitoring interval in seconds (0 disables)."
  type        = number
  default     = 60

  validation {
    condition     = contains([0, 1, 5, 10, 15, 30, 60], var.db_monitoring_interval)
    error_message = "db_monitoring_interval must be one of 0, 1, 5, 10, 15, 30, 60."
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Redis
# -----------------------------------------------------------------------------

variable "redis_engine_version" {
  description = "Redis OSS engine version (7.x)."
  type        = string
  default     = "7.1"

  validation {
    condition     = can(regex("^7\\.[0-9]+$", var.redis_engine_version))
    error_message = "redis_engine_version must look like 7.1."
  }
}

variable "redis_node_type" {
  description = "ElastiCache node type. null = profile default."
  type        = string
  default     = null

  validation {
    condition     = var.redis_node_type == null || can(regex("^cache\\.[a-z0-9]+\\.[a-z0-9]+$", coalesce(var.redis_node_type, "x")))
    error_message = "redis_node_type must look like cache.r6g.large."
  }
}

variable "redis_num_cache_clusters" {
  description = "Nodes in the replication group (primary + replicas), 1-6. null = profile default (saas: 2, with automatic failover and Multi-AZ when > 1)."
  type        = number
  default     = null

  validation {
    condition     = var.redis_num_cache_clusters == null || (coalesce(var.redis_num_cache_clusters, 1) >= 1 && coalesce(var.redis_num_cache_clusters, 1) <= 6)
    error_message = "redis_num_cache_clusters must be between 1 and 6."
  }
}

variable "redis_snapshot_retention_days" {
  description = "Daily snapshot retention (days)."
  type        = number
  default     = 7

  validation {
    condition     = var.redis_snapshot_retention_days >= 1 && var.redis_snapshot_retention_days <= 35
    error_message = "redis_snapshot_retention_days must be between 1 and 35."
  }
}

variable "secret_recovery_window_days" {
  description = "Recovery window for Secrets Manager secrets created by this module (7-30)."
  type        = number
  default     = 30

  validation {
    condition     = var.secret_recovery_window_days >= 7 && var.secret_recovery_window_days <= 30
    error_message = "secret_recovery_window_days must be between 7 and 30."
  }
}

# -----------------------------------------------------------------------------
# S3
# -----------------------------------------------------------------------------

variable "bucket_force_destroy" {
  description = "Allow destroying non-empty buckets. Keep false outside throwaway environments."
  type        = bool
  default     = false
}

variable "s3_noncurrent_version_expiration_days" {
  description = "Days after which noncurrent object versions expire (>= 7)."
  type        = number
  default     = 90

  validation {
    condition     = var.s3_noncurrent_version_expiration_days >= 7
    error_message = "s3_noncurrent_version_expiration_days must be >= 7."
  }
}

# -----------------------------------------------------------------------------
# KMS
# -----------------------------------------------------------------------------

variable "kms_deletion_window_days" {
  description = "Waiting period before a scheduled KMS key deletion (7-30)."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30."
  }
}

variable "app_kms_key_spec" {
  description = "Key spec of the application envelope-encryption key. SYMMETRIC_DEFAULT supports automatic annual rotation; RSA_* keys (asymmetric) CANNOT be auto-rotated by KMS."
  type        = string
  default     = "SYMMETRIC_DEFAULT"

  validation {
    condition     = contains(["SYMMETRIC_DEFAULT", "RSA_2048", "RSA_3072", "RSA_4096"], var.app_kms_key_spec)
    error_message = "app_kms_key_spec must be SYMMETRIC_DEFAULT, RSA_2048, RSA_3072 or RSA_4096."
  }
}

# -----------------------------------------------------------------------------
# WAF
# -----------------------------------------------------------------------------

variable "enable_waf" {
  description = "Create the WAFv2 web ACL for the ALB (attach via the Ingress annotation alb.ingress.kubernetes.io/wafv2-acl-arn). Set false when the customer fronts the ALB themselves."
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "Per-IP request limit per 5 minutes for the rate-based rule (WAF minimum is 10)."
  type        = number
  default     = 2000

  validation {
    condition     = var.waf_rate_limit >= 10 && var.waf_rate_limit <= 20000000
    error_message = "waf_rate_limit must be between 10 and 20000000."
  }
}

variable "waf_managed_rule_groups" {
  description = "AWS managed rule groups to attach (all in vendor AWS), evaluated in list order."
  type        = list(string)
  default = [
    "AWSManagedRulesCommonRuleSet",
    "AWSManagedRulesKnownBadInputsRuleSet",
    "AWSManagedRulesAmazonIpReputationList",
    "AWSManagedRulesSQLiRuleSet",
  ]

  validation {
    condition     = alltrue([for g in var.waf_managed_rule_groups : can(regex("^AWSManagedRules[A-Za-z0-9]+$", g))])
    error_message = "waf_managed_rule_groups entries must be AWS managed rule group names (AWSManagedRules...)."
  }
}

variable "waf_common_ruleset_count_only_rules" {
  description = "Rules inside AWSManagedRulesCommonRuleSet switched to Count instead of Block. Default counts SizeRestrictions_BODY because the stock 8 KB body limit blocks normal time-series uploads; review the WAF logs and tighten for your payload sizes."
  type        = list(string)
  default     = ["SizeRestrictions_BODY"]
}
