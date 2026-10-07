# Two customer-managed keys:
#   data - encryption at rest for RDS, ElastiCache, S3, Secrets Manager, EKS secrets, CloudWatch Logs
#   app  - application envelope encryption (the Tycheon API / control plane wrap data keys with it)

locals {
  account_root_arn = "arn:${local.partition}:iam::${var.account_id}:root"
}

data "aws_iam_policy_document" "kms_data" {
  # Standard "enable IAM policies" statement. Key administration and use is then governed by IAM.
  # Resource "*" in a key policy always means "this key".
  statement {
    sid       = "EnableIAMPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.account_root_arn]
    }
  }

  # CloudWatch Logs (flow logs, EKS control-plane logs, WAF logs) - scoped to this account's log groups.
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${local.region}:${var.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "data" {
  description             = "${local.name} data-at-rest key (RDS, Redis, S3, Secrets Manager, EKS secrets, logs)"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days
  policy                  = data.aws_iam_policy_document.kms_data.json
  tags                    = merge(local.tags, { Name = "${local.name}-data" })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

data "aws_iam_policy_document" "kms_app" {
  statement {
    sid       = "EnableIAMPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.account_root_arn]
    }
  }
}

resource "aws_kms_key" "app" {
  description              = "${local.name} application envelope-encryption key"
  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = var.app_kms_key_spec
  # KMS only supports automatic rotation for symmetric keys.
  enable_key_rotation     = var.app_kms_key_spec == "SYMMETRIC_DEFAULT"
  deletion_window_in_days = var.kms_deletion_window_days
  policy                  = data.aws_iam_policy_document.kms_app.json
  tags                    = merge(local.tags, { Name = "${local.name}-app" })
}

resource "aws_kms_alias" "app" {
  name          = "alias/${local.name}-app"
  target_key_id = aws_kms_key.app.key_id
}
