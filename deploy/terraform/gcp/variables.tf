# -----------------------------------------------------------------------------
# Identity
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID to deploy into."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project ID (6-30 chars, lowercase letters, digits, hyphens)."
  }
}

variable "project_number" {
  description = "Numeric project number. Passed in (not looked up) so plan needs no API call; used to name Google-managed service agents for CMEK grants."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,15}$", var.project_number))
    error_message = "project_number must be the numeric project number."
  }
}

variable "region" {
  description = "Region for all regional resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must look like us-central1."
  }
}

variable "zones" {
  description = "Zones for GKE node pools (and where GPUs are available). Empty = let GKE choose. Check nvidia-l4 availability in your region."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for z in var.zones : can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", z))])
    error_message = "zones entries must look like us-central1-a."
  }
}

variable "name" {
  description = "Short name prefix for resources (lowercase, max 12 chars)."
  type        = string
  default     = "tycheon"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,11}$", var.name))
    error_message = "name must be 2-12 chars: lowercase letters, digits, hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name, e.g. prod (lowercase alphanumerics, max 8 chars)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.environment))
    error_message = "environment must be 2-8 chars: lowercase letters and digits, starting with a letter."
  }
}

variable "labels" {
  description = "Extra labels for every resource that supports them (GCP label syntax: lowercase keys/values)."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.labels : can(regex("^[a-z][a-z0-9_-]{0,62}$", k)) && can(regex("^[a-z0-9_-]{0,63}$", v))])
    error_message = "Label keys must match ^[a-z][a-z0-9_-]{0,62}$ and values ^[a-z0-9_-]{0,63}$."
  }
}

variable "deletion_protection" {
  description = "Deletion protection for the GKE cluster and Cloud SQL instance. Set false only for throwaway environments."
  type        = bool
  default     = true
}

variable "enable_apis" {
  description = "Enable the required Google APIs through google_project_service (never disabled on destroy)."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Network
# -----------------------------------------------------------------------------

variable "subnet_cidr" {
  description = "Primary range of the node subnet."
  type        = string
  default     = "10.50.0.0/20"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0))
    error_message = "subnet_cidr must be a valid CIDR."
  }
}

variable "pods_cidr" {
  description = "Secondary range for pods."
  type        = string
  default     = "10.60.0.0/16"

  validation {
    condition     = can(cidrhost(var.pods_cidr, 0))
    error_message = "pods_cidr must be a valid CIDR."
  }
}

variable "services_cidr" {
  description = "Secondary range for Kubernetes services."
  type        = string
  default     = "10.61.0.0/20"

  validation {
    condition     = can(cidrhost(var.services_cidr, 0))
    error_message = "services_cidr must be a valid CIDR."
  }
}

variable "master_ipv4_cidr" {
  description = "/28 for the private GKE control plane."
  type        = string
  default     = "172.16.0.0/28"

  validation {
    condition     = can(cidrhost(var.master_ipv4_cidr, 0)) && endswith(var.master_ipv4_cidr, "/28")
    error_message = "master_ipv4_cidr must be a valid /28 CIDR."
  }
}

variable "private_service_access_prefix_length" {
  description = "Prefix length of the range reserved for private service access (Cloud SQL, Memorystore)."
  type        = number
  default     = 20

  validation {
    condition     = var.private_service_access_prefix_length >= 16 && var.private_service_access_prefix_length <= 24
    error_message = "private_service_access_prefix_length must be between 16 and 24."
  }
}

variable "flow_log_sampling" {
  description = "VPC flow log sampling rate for the node subnet (0 disables aggregation sampling, up to 1.0)."
  type        = number
  default     = 0.5

  validation {
    condition     = var.flow_log_sampling > 0 && var.flow_log_sampling <= 1
    error_message = "flow_log_sampling must be in (0, 1]."
  }
}

# -----------------------------------------------------------------------------
# GKE
# -----------------------------------------------------------------------------

variable "release_channel" {
  description = "GKE release channel."
  type        = string
  default     = "REGULAR"

  validation {
    condition     = contains(["RAPID", "REGULAR", "STABLE"], var.release_channel)
    error_message = "release_channel must be RAPID, REGULAR or STABLE."
  }
}

variable "enable_private_endpoint" {
  description = "Make the GKE control-plane endpoint private-only (reachable from the VPC / authorized networks via peering, VPN)."
  type        = bool
  default     = true
}

variable "master_authorized_networks" {
  description = "Networks allowed to reach the Kubernetes API. Required and non-empty; 0.0.0.0/0 is rejected."
  type = list(object({
    cidr = string
    name = string
  }))

  validation {
    condition     = length(var.master_authorized_networks) > 0
    error_message = "master_authorized_networks must contain at least one entry."
  }
  validation {
    condition     = alltrue([for n in var.master_authorized_networks : can(cidrhost(n.cidr, 0)) && n.cidr != "0.0.0.0/0"])
    error_message = "master_authorized_networks entries must be valid CIDRs and must not be 0.0.0.0/0."
  }
}

variable "cpu_node_pool" {
  description = "CPU node pool. min/max are TOTAL nodes across zones."
  type = object({
    machine_type = optional(string, "n2-standard-8")
    min_nodes    = optional(number, 1)
    max_nodes    = optional(number, 12)
    disk_size_gb = optional(number, 100)
    spot         = optional(bool, false)
  })
  default = {}

  validation {
    condition     = length(trimspace(var.cpu_node_pool.machine_type)) > 0
    error_message = "cpu_node_pool.machine_type must be non-empty."
  }
  validation {
    condition     = var.cpu_node_pool.min_nodes >= 1 && var.cpu_node_pool.max_nodes >= var.cpu_node_pool.min_nodes
    error_message = "cpu_node_pool requires 1 <= min_nodes <= max_nodes."
  }
  validation {
    condition     = var.cpu_node_pool.disk_size_gb >= 20
    error_message = "cpu_node_pool.disk_size_gb must be >= 20."
  }
}

variable "gpu_node_pool" {
  description = "GPU node pool (nvidia-l4 by default; tainted nvidia.com/gpu=true:NoSchedule; label tycheon.io/pool=gpu). Scales to zero."
  type = object({
    enabled           = optional(bool, true)
    machine_type      = optional(string, "g2-standard-8")
    accelerator_type  = optional(string, "nvidia-l4")
    accelerator_count = optional(number, 1)
    min_nodes         = optional(number, 0)
    max_nodes         = optional(number, 4)
    disk_size_gb      = optional(number, 200)
    spot              = optional(bool, false)
  })
  default = {}

  validation {
    condition     = length(trimspace(var.gpu_node_pool.machine_type)) > 0
    error_message = "gpu_node_pool.machine_type must be non-empty."
  }
  validation {
    condition     = startswith(var.gpu_node_pool.accelerator_type, "nvidia-") && var.gpu_node_pool.accelerator_count >= 1
    error_message = "gpu_node_pool.accelerator_type must start with nvidia- and accelerator_count must be >= 1."
  }
  validation {
    condition     = var.gpu_node_pool.min_nodes >= 0 && var.gpu_node_pool.max_nodes >= 1 && var.gpu_node_pool.max_nodes >= var.gpu_node_pool.min_nodes
    error_message = "gpu_node_pool requires 0 <= min_nodes <= max_nodes and max_nodes >= 1."
  }
  validation {
    condition     = var.gpu_node_pool.disk_size_gb >= 20
    error_message = "gpu_node_pool.disk_size_gb must be >= 20."
  }
}

variable "k8s_namespace" {
  description = "Namespace the Tycheon Helm release is installed into (Workload Identity binding)."
  type        = string
  default     = "tycheon"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.k8s_namespace))
    error_message = "k8s_namespace must be a valid Kubernetes namespace name."
  }
}

variable "service_account_names" {
  description = "Kubernetes ServiceAccount names bound to the api / control-plane / worker Google service accounts."
  type = object({
    api           = optional(string, "tycheon-api")
    control_plane = optional(string, "tycheon-control-plane")
    worker        = optional(string, "tycheon-worker")
  })
  default = {}
}

# -----------------------------------------------------------------------------
# Cloud SQL
# -----------------------------------------------------------------------------

variable "db_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-4-16384"

  validation {
    condition     = length(trimspace(var.db_tier)) > 0
    error_message = "db_tier must be non-empty."
  }
}

variable "db_edition" {
  description = "Cloud SQL edition."
  type        = string
  default     = "ENTERPRISE"

  validation {
    condition     = contains(["ENTERPRISE", "ENTERPRISE_PLUS"], var.db_edition)
    error_message = "db_edition must be ENTERPRISE or ENTERPRISE_PLUS."
  }
}

variable "db_availability_type" {
  description = "REGIONAL (HA, recommended) or ZONAL."
  type        = string
  default     = "REGIONAL"

  validation {
    condition     = contains(["REGIONAL", "ZONAL"], var.db_availability_type)
    error_message = "db_availability_type must be REGIONAL or ZONAL."
  }
}

variable "db_disk_size_gb" {
  description = "Initial disk size (GiB); auto-resize is enabled."
  type        = number
  default     = 100

  validation {
    condition     = var.db_disk_size_gb >= 10 && var.db_disk_size_gb <= 65536
    error_message = "db_disk_size_gb must be between 10 and 65536."
  }
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

variable "db_backup_retention_count" {
  description = "Number of automated backups to retain (>= 7). Point-in-time recovery is always on."
  type        = number
  default     = 14

  validation {
    condition     = var.db_backup_retention_count >= 7 && var.db_backup_retention_count <= 365
    error_message = "db_backup_retention_count must be between 7 and 365."
  }
}

# -----------------------------------------------------------------------------
# Memorystore Redis
# -----------------------------------------------------------------------------

variable "redis_tier" {
  description = "BASIC or STANDARD_HA."
  type        = string
  default     = "STANDARD_HA"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.redis_tier)
    error_message = "redis_tier must be BASIC or STANDARD_HA."
  }
}

variable "redis_memory_size_gb" {
  description = "Redis memory (GiB)."
  type        = number
  default     = 5

  validation {
    condition     = var.redis_memory_size_gb >= 1 && var.redis_memory_size_gb <= 300
    error_message = "redis_memory_size_gb must be between 1 and 300."
  }
}

variable "redis_version" {
  description = "Memorystore Redis version."
  type        = string
  default     = "REDIS_7_2"

  validation {
    condition     = can(regex("^REDIS_[0-9]+_[0-9]+$", var.redis_version))
    error_message = "redis_version must look like REDIS_7_2."
  }
}

# -----------------------------------------------------------------------------
# GCS / KMS
# -----------------------------------------------------------------------------

variable "bucket_force_destroy" {
  description = "Allow destroying non-empty buckets. Keep false outside throwaway environments."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "Days after which noncurrent object versions are deleted (>= 7)."
  type        = number
  default     = 90

  validation {
    condition     = var.noncurrent_version_expiration_days >= 7
    error_message = "noncurrent_version_expiration_days must be >= 7."
  }
}

variable "kms_rotation_period_days" {
  description = "Automatic key rotation period in days (1-3650)."
  type        = number
  default     = 90

  validation {
    condition     = var.kms_rotation_period_days >= 1 && var.kms_rotation_period_days <= 3650
    error_message = "kms_rotation_period_days must be between 1 and 3650."
  }
}

variable "kms_protection_level" {
  description = "SOFTWARE or HSM."
  type        = string
  default     = "SOFTWARE"

  validation {
    condition     = contains(["SOFTWARE", "HSM"], var.kms_protection_level)
    error_message = "kms_protection_level must be SOFTWARE or HSM."
  }
}

# -----------------------------------------------------------------------------
# Load balancer / Cloud Armor
# -----------------------------------------------------------------------------

variable "enable_lb_resources" {
  description = "Create the global static IP, managed certificate, SSL policy and Cloud Armor policy consumed by the GKE Ingress."
  type        = bool
  default     = true
}

variable "lb_domains" {
  description = "Domains for the Google-managed certificate (required when enable_lb_resources = true)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for d in var.lb_domains : can(regex("^([a-z0-9]([-a-z0-9]*[a-z0-9])?\\.)+[a-z]{2,}$", d))])
    error_message = "lb_domains entries must be valid lowercase DNS names."
  }
}

variable "armor_rate_limit_per_minute" {
  description = "Per-IP request limit per minute for the Cloud Armor throttle rule."
  type        = number
  default     = 600

  validation {
    condition     = var.armor_rate_limit_per_minute >= 10
    error_message = "armor_rate_limit_per_minute must be >= 10."
  }
}

variable "armor_waf_rules" {
  description = "Cloud Armor preconfigured WAF rule sets to deny on match."
  type        = list(string)
  default     = ["sqli-v33-stable", "xss-v33-stable", "lfi-v33-stable", "rce-v33-stable", "scannerdetection-v33-stable", "protocolattack-v33-stable"]

  validation {
    condition     = alltrue([for r in var.armor_waf_rules : can(regex("^[a-z]+-v[0-9]+-stable$", r))])
    error_message = "armor_waf_rules entries must look like sqli-v33-stable."
  }
}
