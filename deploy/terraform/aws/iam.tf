# IRSA roles: add-ons (vpc-cni, ebs-csi, load-balancer-controller, cluster-autoscaler)
# and Tycheon workloads (api, control-plane, worker).
#
# Wildcard (Resource = "*") statements exist only where AWS has no resource-level
# permission support; each one is called out in a comment below.

locals {
  irsa_roles = merge(
    {
      vpc_cni = {
        namespace       = "kube-system"
        service_account = "aws-node"
      }
      ebs_csi = {
        namespace       = "kube-system"
        service_account = "ebs-csi-controller-sa"
      }
      lb_controller = {
        namespace       = "kube-system"
        service_account = "aws-load-balancer-controller"
      }
      api = {
        namespace       = var.k8s_namespace
        service_account = var.service_account_names.api
      }
      control_plane = {
        namespace       = var.k8s_namespace
        service_account = var.service_account_names.control_plane
      }
      worker = {
        namespace       = var.k8s_namespace
        service_account = var.service_account_names.worker
      }
    },
    var.enable_cluster_autoscaler_role ? {
      cluster_autoscaler = {
        namespace       = "kube-system"
        service_account = "cluster-autoscaler"
      }
    } : {},
  )
}

resource "aws_iam_role" "irsa" {
  for_each = local.irsa_roles

  name = "${local.name}-${replace(each.key, "_", "-")}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_host}:sub" = "system:serviceaccount:${each.value.namespace}:${each.value.service_account}"
        }
      }
    }]
  })
  tags = merge(local.tags, { "tycheon.io/irsa" = each.key })
}

# ---------------------------- vpc-cni / ebs-csi (AWS managed policies) ------
resource "aws_iam_role_policy_attachment" "vpc_cni" {
  role       = aws_iam_role.irsa["vpc_cni"].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.irsa["ebs_csi"].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# ---------------------------- AWS Load Balancer Controller ------------------
# Policy mirrors the upstream controller policy (kubernetes-sigs/aws-load-balancer-controller,
# docs/install/iam_policy.json). Upstream requires Resource "*" for the Describe* calls, the
# SG authorize/revoke calls and the ELB mutation calls (which are instead constrained by
# elbv2.k8s.aws/cluster resource-tag conditions). Diff against the release matching the
# controller version you deploy. Wildcard ARNs use "arn:*:" so the policy is partition-neutral.
resource "aws_iam_policy" "lb_controller" {
  name        = "${local.name}-lb-controller"
  description = "AWS Load Balancer Controller for ${local.name}"
  policy      = jsonencode(jsondecode(file("${path.module}/policies/aws-load-balancer-controller.json")))
  tags        = local.tags
}

resource "aws_iam_role_policy_attachment" "lb_controller" {
  role       = aws_iam_role.irsa["lb_controller"].name
  policy_arn = aws_iam_policy.lb_controller.arn
}

# ---------------------------- Cluster Autoscaler ----------------------------
data "aws_iam_policy_document" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler_role ? 1 : 0

  # Describe/List APIs do not support resource-level permissions: Resource "*" is required by AWS.
  statement {
    sid    = "DescribeRequiresWildcard"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeLaunchConfigurations",
      "autoscaling:DescribeScalingActivities",
      "autoscaling:DescribeTags",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:GetInstanceTypesFromInstanceRequirements",
      "eks:DescribeNodegroup",
    ]
    resources = ["*"]
  }

  # Mutations limited to ASGs tagged as owned by THIS cluster.
  statement {
    sid    = "ScaleOwnedAutoScalingGroups"
    effect = "Allow"
    actions = [
      "autoscaling:SetDesiredCapacity",
      "autoscaling:TerminateInstanceInAutoScalingGroup",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/k8s.io/cluster-autoscaler/${local.name}"
      values   = ["owned"]
    }
  }
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler_role ? 1 : 0

  name   = "cluster-autoscaler"
  role   = aws_iam_role.irsa["cluster_autoscaler"].id
  policy = data.aws_iam_policy_document.cluster_autoscaler[0].json
}

# ---------------------------- Tycheon workloads -----------------------------
locals {
  # prefix "" means the whole bucket. write = true additionally grants PutObject/AbortMultipartUpload.
  s3_grants = {
    api = [
      { bucket = "models", prefix = "", write = false },
      { bucket = "data", prefix = "uploads/", write = true },
    ]
    control_plane = [
      { bucket = "models", prefix = "registry/", write = true },
      { bucket = "models", prefix = "", write = false },
      { bucket = "data", prefix = "", write = false },
    ]
    worker = [
      { bucket = "models", prefix = "", write = false },
      { bucket = "models", prefix = "runs/", write = true },
      { bucket = "data", prefix = "", write = false },
      { bucket = "data", prefix = "scratch/", write = true },
    ]
  }

  # api and control-plane wrap/unwrap data keys; the worker may only unwrap/derive.
  app_kms_actions = {
    api           = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    control_plane = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    worker        = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
  }
}

data "aws_iam_policy_document" "workload" {
  for_each = local.s3_grants

  statement {
    sid       = "ReadObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = distinct([for g in each.value : "${aws_s3_bucket.this[g.bucket].arn}/${g.prefix}*"])
  }

  statement {
    sid       = "WriteObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = distinct([for g in each.value : "${aws_s3_bucket.this[g.bucket].arn}/${g.prefix}*" if g.write])
  }

  statement {
    sid       = "ListGrantedPrefixes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = distinct([for g in each.value : aws_s3_bucket.this[g.bucket].arn])
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = distinct([for g in each.value : "${g.prefix}*"])
    }
  }

  # Data key: only through S3 / Secrets Manager (kms:ViaService), never directly.
  statement {
    sid       = "UseDataKeyViaServices"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [aws_kms_key.data.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "s3.${local.region}.amazonaws.com",
        "secretsmanager.${local.region}.amazonaws.com",
      ]
    }
  }

  statement {
    sid       = "UseAppEnvelopeKey"
    effect    = "Allow"
    actions   = local.app_kms_actions[each.key]
    resources = [aws_kms_key.app.arn]
  }

  statement {
    sid     = "ReadBackendSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      aws_db_instance.this.master_user_secret[0].secret_arn,
      aws_secretsmanager_secret.redis_auth.arn,
    ]
  }

  statement {
    sid       = "ConnectToDatabaseWithIamAuth"
    effect    = "Allow"
    actions   = ["rds-db:connect"]
    resources = ["arn:${local.partition}:rds-db:${local.region}:${var.account_id}:dbuser:${aws_db_instance.this.resource_id}/${var.db_iam_username}"]
  }
}

resource "aws_iam_role_policy" "workload" {
  for_each = local.s3_grants

  name   = "tycheon-${replace(each.key, "_", "-")}"
  role   = aws_iam_role.irsa[each.key].id
  policy = data.aws_iam_policy_document.workload[each.key].json
}
