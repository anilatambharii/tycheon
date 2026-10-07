# Tycheon inside a customer-owned VPC (profile = "customer_vpc").
#
# - No VPC/NAT/IGW is created; EKS, RDS and Redis land in the subnets you pass in.
# - The customer VPC must provide egress (NAT or VPC endpoints) for private subnets and tag
#   its public subnets kubernetes.io/role/elb=1 / private subnets kubernetes.io/role/internal-elb=1
#   so the AWS Load Balancer Controller can place the ALB.
# - WAF is off here because the customer fronts the ALB with their own controls.

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

variable "existing_vpc_id" {
  type = string
}

variable "existing_private_subnet_ids" {
  type = list(string)
}

variable "alb_ingress_cidrs" {
  description = "Customer networks allowed to reach the ALB on 443 (keep this narrow; default is RFC1918 only)."
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "eks_admin_role_arns" {
  type    = list(string)
  default = []
}

variable "enable_waf" {
  type    = bool
  default = false
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

  profile     = "customer_vpc"
  environment = var.environment
  account_id  = var.account_id

  existing_vpc_id             = var.existing_vpc_id
  existing_private_subnet_ids = var.existing_private_subnet_ids

  alb_ingress_cidrs   = var.alb_ingress_cidrs
  eks_admin_role_arns = var.eks_admin_role_arns
  enable_waf          = var.enable_waf

  # Customer VPC admins usually reach the API over VPN/Direct Connect: keep the endpoint private.
  endpoint_public_access = false

  # Smaller GPU pool by default for customer installs.
  gpu_node_group = {
    max_size = 2
  }

  tags = var.tags
}

output "helm_values" {
  description = "Non-secret values for the Tycheon Helm chart."
  value       = module.tycheon.helm_values
}

output "cluster_name" {
  value = module.tycheon.cluster_name
}
