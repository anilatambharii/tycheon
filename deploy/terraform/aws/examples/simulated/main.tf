# SIMULATED apply: the same module as examples/complete, pointed at a local AWS emulator (moto) so
# `terraform apply` and `terraform destroy` really execute every create/read/delete call against an
# API. It checks dependency ordering, argument shapes and idempotency that `plan` cannot. It does
# NOT prove anything about real AWS: no real EKS, RDS, KMS, IAM evaluation, quotas or networking.
# The workflow applies only the resources the emulator implements (it has no EKS/RDS/ElastiCache
# clusters and hangs on an ElastiCache parameter group); see .github/workflows/terraform-simulated-apply.yml.
#
#   pip install "moto[server]" && moto_server -p 5000 &
#   terraform init && terraform apply -var-file=simulated.tfvars -auto-approve && terraform destroy -auto-approve -var-file=simulated.tfvars

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

variable "emulator_url" {
  type    = string
  default = "http://127.0.0.1:5000"
}

variable "profile" {
  type    = string
  default = "saas"
}

provider "aws" {
  region = "us-east-1" # credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (dummy values)

  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  s3_use_path_style           = true

  endpoints {
    ec2            = var.emulator_url
    eks            = var.emulator_url
    iam            = var.emulator_url
    kms            = var.emulator_url
    s3             = var.emulator_url
    sts            = var.emulator_url
    rds            = var.emulator_url
    elasticache    = var.emulator_url
    wafv2          = var.emulator_url
    logs           = var.emulator_url
    secretsmanager = var.emulator_url
    autoscaling    = var.emulator_url
  }
}

module "tycheon" {
  source = "../.."

  profile     = var.profile
  environment = "sim"
  account_id  = "123456789012"

  availability_zones = ["us-east-1a", "us-east-1b", "us-east-1c"]
  vpc_cidr           = "10.40.0.0/16"
  alb_ingress_cidrs  = ["10.0.0.0/8"]

  eks_admin_role_arns = []
  gpu_node_group      = {}

  enable_waf = true
  tags       = { Simulated = "true" }
}

output "cluster_name" {
  value = module.tycheon.cluster_name
}
