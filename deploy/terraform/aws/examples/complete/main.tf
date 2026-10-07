# Full Tycheon Cloud (profile = "saas") deployment.
#
#   cp terraform.tfvars.example terraform.tfvars   # edit; contains no secrets
#   terraform init && terraform plan -var-file=terraform.tfvars
#
# Credentials come from the standard AWS environment/profile mechanisms; nothing is
# configured here. `offline_validation` exists only so CI/verify.sh can run
# `terraform plan` with dummy credentials and no network access to AWS.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }

  # Remote state - see ../../../README.md for the commented S3 + DynamoDB example.
}

variable "offline_validation" {
  description = "Set true ONLY for credential-less plan checks (skips credential/account/region validation in the provider)."
  type        = bool
  default     = false
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "availability_zones" {
  type = list(string)
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "eks_admin_role_arns" {
  type    = list(string)
  default = []
}

variable "alb_ingress_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "gpu_node_group" {
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
}

variable "tags" {
  type    = map(string)
  default = {}
}

provider "aws" {
  region = var.region

  skip_credentials_validation = var.offline_validation
  skip_requesting_account_id  = var.offline_validation
  skip_metadata_api_check     = var.offline_validation
  skip_region_validation      = var.offline_validation

  default_tags {
    tags = {
      Application = "tycheon"
      ManagedBy   = "terraform"
    }
  }
}

module "tycheon" {
  source = "../.."

  profile     = "saas"
  environment = var.environment
  account_id  = var.account_id

  availability_zones = var.availability_zones
  vpc_cidr           = var.vpc_cidr
  alb_ingress_cidrs  = var.alb_ingress_cidrs

  eks_admin_role_arns = var.eks_admin_role_arns
  gpu_node_group      = var.gpu_node_group

  enable_waf = true
  tags       = var.tags
}

output "helm_values" {
  description = "Non-secret values for the Tycheon Helm chart."
  value       = module.tycheon.helm_values
}

output "cluster_name" {
  value = module.tycheon.cluster_name
}

output "kms_key_arns" {
  value = module.tycheon.kms_key_arns
}
