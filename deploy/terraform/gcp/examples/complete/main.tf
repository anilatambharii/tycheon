# Full Tycheon Cloud deployment on GCP.
#
#   cp terraform.tfvars.example terraform.tfvars   # edit; contains no secrets
#   terraform init && terraform plan -var-file=terraform.tfvars
#
# Credentials come from Application Default Credentials / the environment; nothing is configured
# here. `offline_validation` exists only so verify.sh can run `terraform plan` with a dummy token.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.20, < 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.20, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }

  # Remote state - see ../../../README.md for the commented GCS backend example.
}

variable "offline_validation" {
  description = "Set true ONLY for credential-less plan checks (disables API-touching provider behaviour such as request batching)."
  type        = bool
  default     = false
}

variable "project_id" {
  type = string
}

variable "project_number" {
  type = string
}

variable "region" {
  type = string
}

variable "zones" {
  type    = list(string)
  default = []
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "master_authorized_networks" {
  type = list(object({
    cidr = string
    name = string
  }))
}

variable "lb_domains" {
  type = list(string)
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "gpu_node_pool" {
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
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Without this, the provider batches some API calls; irrelevant to plan but harmless to disable offline.
  batching {
    enable_batching = !var.offline_validation
  }
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

module "tycheon" {
  source = "../.."

  providers = {
    google      = google
    google-beta = google-beta
  }

  project_id     = var.project_id
  project_number = var.project_number
  region         = var.region
  zones          = var.zones
  environment    = var.environment

  master_authorized_networks = var.master_authorized_networks
  lb_domains                 = var.lb_domains
  gpu_node_pool              = var.gpu_node_pool

  labels = var.labels
}

output "helm_values" {
  description = "Non-secret values for the Tycheon Helm chart."
  value       = module.tycheon.helm_values
}

output "cluster_name" {
  value = module.tycheon.cluster_name
}

output "kms_key_ids" {
  value = module.tycheon.kms_key_ids
}
