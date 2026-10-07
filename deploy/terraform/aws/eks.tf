# EKS cluster, OIDC provider, managed node groups (CPU + GPU) and core add-ons.

locals {
  gpu_enabled = var.gpu_node_group.enabled

  node_groups = merge(
    {
      cpu = {
        instance_types = local.cpu_node_group.instance_types
        min_size       = local.cpu_node_group.min_size
        max_size       = local.cpu_node_group.max_size
        desired_size   = local.cpu_node_group.desired_size
        capacity_type  = local.cpu_node_group.capacity_type
        disk_size_gb   = local.cpu_node_group.disk_size_gb
        ami_type       = local.cpu_node_group.ami_type
        labels         = { "tycheon.io/pool" = "cpu" }
        taints         = []
      }
    },
    local.gpu_enabled ? {
      gpu = {
        instance_types = var.gpu_node_group.instance_types
        min_size       = var.gpu_node_group.min_size
        max_size       = var.gpu_node_group.max_size
        desired_size   = var.gpu_node_group.desired_size
        capacity_type  = var.gpu_node_group.capacity_type
        disk_size_gb   = var.gpu_node_group.disk_size_gb
        ami_type       = var.gpu_node_group.ami_type
        labels         = { "tycheon.io/pool" = "gpu" }
        taints         = [{ key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }]
      }
    } : {},
  )

  oidc_issuer_url = aws_eks_cluster.this.identity[0].oidc[0].issuer
  oidc_host       = replace(local.oidc_issuer_url, "https://", "")
}

# ---------------------------- Cluster IAM role ------------------------------
resource "aws_iam_role" "cluster" {
  name = "${local.name}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ---------------------------- Control-plane logs ----------------------------
resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${local.name}/cluster"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.data.arn
  tags              = local.tags
}

# ---------------------------- Cluster ---------------------------------------
resource "aws_eks_cluster" "this" {
  name                      = local.name
  version                   = var.kubernetes_version
  role_arn                  = aws_iam_role.cluster.arn
  enabled_cluster_log_types = var.cluster_log_types

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids              = local.private_subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = var.endpoint_public_access
    public_access_cidrs     = var.endpoint_public_access ? var.public_access_cidrs : null
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.data.arn
    }
  }

  tags = merge(local.tags, { Name = local.name })

  depends_on = [
    aws_iam_role_policy_attachment.cluster,
    aws_cloudwatch_log_group.eks,
    terraform_data.input_guards,
  ]
}

# Cluster-admin for named IAM principals (access entries; no aws-auth ConfigMap).
resource "aws_eks_access_entry" "admin" {
  for_each = toset(var.eks_admin_role_arns)

  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value
  type          = "STANDARD"
  tags          = local.tags
}

resource "aws_eks_access_policy_association" "admin" {
  for_each = toset(var.eks_admin_role_arns)

  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value
  policy_arn    = "arn:${local.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.admin]
}

# ---------------------------- OIDC provider (IRSA) --------------------------
resource "aws_iam_openid_connect_provider" "eks" {
  url            = local.oidc_issuer_url
  client_id_list = ["sts.amazonaws.com"]
  tags           = merge(local.tags, { Name = "${local.name}-eks-irsa" })
}

# ---------------------------- Node IAM role ---------------------------------
resource "aws_iam_role" "node" {
  name = "${local.name}-eks-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "AmazonEKSWorkerNodePolicy",
    "AmazonEC2ContainerRegistryPullOnly",
  ])

  role       = aws_iam_role.node.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/${each.value}"
}

# ---------------------------- Node groups -----------------------------------
resource "aws_launch_template" "node" {
  for_each = local.node_groups

  name_prefix            = "${local.name}-${each.key}-"
  update_default_version = true

  # IMDSv2 only, hop limit 1: pods cannot reach node credentials. Workloads use IRSA.
  # (AWS Load Balancer Controller / EBS CSI therefore need region + VPC id as chart values; see outputs.)
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = each.value.disk_size_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  monitoring {
    enabled = true
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${local.name}-${each.key}" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.tags, { Name = "${local.name}-${each.key}" })
  }

  tags = local.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_eks_node_group" "this" {
  for_each = local.node_groups

  cluster_name         = aws_eks_cluster.this.name
  node_group_name      = "${local.name}-${each.key}"
  node_role_arn        = aws_iam_role.node.arn
  subnet_ids           = local.private_subnet_ids
  instance_types       = each.value.instance_types
  ami_type             = each.value.ami_type
  capacity_type        = each.value.capacity_type
  labels               = each.value.labels
  force_update_version = false

  launch_template {
    id      = aws_launch_template.node[each.key].id
    version = aws_launch_template.node[each.key].latest_version
  }

  scaling_config {
    min_size     = each.value.min_size
    max_size     = each.value.max_size
    desired_size = each.value.desired_size
  }

  update_config {
    max_unavailable = 1
  }

  dynamic "taint" {
    for_each = each.value.taints
    content {
      key    = taint.value.key
      value  = taint.value.value
      effect = taint.value.effect
    }
  }

  # Cluster Autoscaler discovery. The node-template tags let it scale a pool FROM ZERO
  # (it must know labels and taints before any node exists).
  tags = merge(
    local.tags,
    {
      "k8s.io/cluster-autoscaler/enabled"                             = "true"
      "k8s.io/cluster-autoscaler/${local.name}"                       = "owned"
      "k8s.io/cluster-autoscaler/node-template/label/tycheon.io/pool" = each.key
    },
    each.key == "gpu" ? {
      "k8s.io/cluster-autoscaler/node-template/taint/nvidia.com/gpu"     = "true:NoSchedule"
      "k8s.io/cluster-autoscaler/node-template/resources/nvidia.com/gpu" = "1"
    } : {},
  )

  lifecycle {
    # The autoscaler owns desired_size after creation.
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [
    aws_iam_role_policy_attachment.node,
    aws_eks_addon.before_compute,
  ]
}

# ---------------------------- Add-ons ---------------------------------------
resource "aws_eks_addon" "before_compute" {
  for_each = {
    "vpc-cni"    = aws_iam_role.irsa["vpc_cni"].arn
    "kube-proxy" = null
  }

  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.key
  service_account_role_arn    = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = local.tags
}

resource "aws_eks_addon" "after_compute" {
  for_each = {
    "coredns"            = null
    "aws-ebs-csi-driver" = aws_iam_role.irsa["ebs_csi"].arn
  }

  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.key
  service_account_role_arn    = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = local.tags

  depends_on = [aws_eks_node_group.this]
}
