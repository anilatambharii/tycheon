# Tycheon on AWS - root of the reusable module.
#
# This module deliberately does NOT configure providers. The caller supplies the
# `aws` provider (see examples/). Only data sources that need no AWS API call are
# used (partition and region come from provider configuration), so `terraform plan`
# works offline when account_id / availability_zones are passed in.

data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  name      = "${var.name}-${var.environment}"
  partition = data.aws_partition.current.partition
  region    = data.aws_region.current.region

  tags = merge(
    {
      Application          = "tycheon"
      Environment          = var.environment
      Profile              = var.profile
      ManagedBy            = "terraform"
      "tycheon.io/module"  = "aws"
      "tycheon.io/cluster" = local.name
    },
    var.tags,
  )

  create_vpc = var.existing_vpc_id == null

  # Profile defaults. Every value is overridable by the explicit variable.
  profile_defaults = {
    saas = {
      single_nat_gateway       = false
      cpu_instance_types       = ["m6i.2xlarge"]
      cpu_min_size             = 3
      cpu_max_size             = 12
      cpu_desired_size         = 3
      db_instance_class        = "db.r6g.xlarge"
      db_allocated_storage_gb  = 200
      db_multi_az              = true
      db_deletion_protection   = true
      db_backup_retention_days = 35
      redis_node_type          = "cache.r6g.large"
      redis_num_cache_clusters = 2
    }
    customer_vpc = {
      single_nat_gateway       = true
      cpu_instance_types       = ["m6i.xlarge"]
      cpu_min_size             = 2
      cpu_max_size             = 6
      cpu_desired_size         = 2
      db_instance_class        = "db.m6g.large"
      db_allocated_storage_gb  = 100
      db_multi_az              = false
      db_deletion_protection   = true
      db_backup_retention_days = 14
      redis_node_type          = "cache.m6g.large"
      redis_num_cache_clusters = 1
    }
  }
  d = local.profile_defaults[var.profile]

  single_nat_gateway       = var.single_nat_gateway != null ? var.single_nat_gateway : local.d.single_nat_gateway
  db_instance_class        = var.db_instance_class != null ? var.db_instance_class : local.d.db_instance_class
  db_allocated_storage_gb  = var.db_allocated_storage_gb != null ? var.db_allocated_storage_gb : local.d.db_allocated_storage_gb
  db_max_storage_gb        = var.db_max_allocated_storage_gb != null ? var.db_max_allocated_storage_gb : local.db_allocated_storage_gb * 4
  db_multi_az              = var.db_multi_az != null ? var.db_multi_az : local.d.db_multi_az
  db_deletion_protection   = var.db_deletion_protection != null ? var.db_deletion_protection : local.d.db_deletion_protection
  db_backup_retention_days = var.db_backup_retention_days != null ? var.db_backup_retention_days : local.d.db_backup_retention_days
  redis_node_type          = var.redis_node_type != null ? var.redis_node_type : local.d.redis_node_type
  redis_num_cache_clusters = var.redis_num_cache_clusters != null ? var.redis_num_cache_clusters : local.d.redis_num_cache_clusters
  redis_ha                 = local.redis_num_cache_clusters > 1

  cpu_node_group = {
    instance_types = var.cpu_node_group.instance_types != null ? var.cpu_node_group.instance_types : local.d.cpu_instance_types
    min_size       = var.cpu_node_group.min_size != null ? var.cpu_node_group.min_size : local.d.cpu_min_size
    max_size       = var.cpu_node_group.max_size != null ? var.cpu_node_group.max_size : local.d.cpu_max_size
    desired_size   = var.cpu_node_group.desired_size != null ? var.cpu_node_group.desired_size : local.d.cpu_desired_size
    capacity_type  = var.cpu_node_group.capacity_type
    disk_size_gb   = var.cpu_node_group.disk_size_gb
    ami_type       = var.cpu_node_group.ami_type
  }

  private_subnet_ids = local.create_vpc ? aws_subnet.private[*].id : var.existing_private_subnet_ids
  vpc_id             = local.create_vpc ? aws_vpc.this[0].id : var.existing_vpc_id
}

# Input guards that cross variable boundaries (evaluated at plan time).
resource "terraform_data" "input_guards" {
  input = local.name

  lifecycle {
    precondition {
      condition     = var.profile != "customer_vpc" || var.existing_vpc_id != null
      error_message = "profile = \"customer_vpc\" requires existing_vpc_id and existing_private_subnet_ids."
    }
    precondition {
      condition     = local.create_vpc || length(var.existing_private_subnet_ids) >= 2
      error_message = "existing_private_subnet_ids must contain at least 2 subnets in different AZs when existing_vpc_id is set."
    }
    precondition {
      condition     = !local.create_vpc || length(var.availability_zones) >= 3
      error_message = "availability_zones must list at least 3 AZs when the module creates the VPC."
    }
    precondition {
      condition     = !var.endpoint_public_access || (length(var.public_access_cidrs) > 0 && !contains(var.public_access_cidrs, "0.0.0.0/0"))
      error_message = "endpoint_public_access = true requires public_access_cidrs, and 0.0.0.0/0 is not allowed."
    }
    precondition {
      condition     = local.cpu_node_group.min_size <= local.cpu_node_group.desired_size && local.cpu_node_group.desired_size <= local.cpu_node_group.max_size
      error_message = "cpu_node_group requires min_size <= desired_size <= max_size."
    }
    precondition {
      condition     = local.db_max_storage_gb >= local.db_allocated_storage_gb
      error_message = "db_max_allocated_storage_gb must be >= db_allocated_storage_gb."
    }
    precondition {
      condition     = !local.db_multi_az || length(local.private_subnet_ids) >= 2 || !local.create_vpc
      error_message = "Multi-AZ RDS needs subnets in at least two AZs."
    }
  }
}
