# Outputs consumed by the Tycheon Helm chart (see helm_values for a ready-to-paste map).
# No secret values are exported; only ARNs / identifiers / endpoints.

output "region" {
  description = "AWS region (needed by the AWS Load Balancer Controller chart: region)."
  value       = local.region
}

output "vpc_id" {
  description = "VPC ID (needed by the AWS Load Balancer Controller chart: vpcId)."
  value       = local.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs used by EKS, RDS and Redis."
  value       = local.private_subnet_ids
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 cluster CA (public certificate)."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN for IRSA."
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "oidc_issuer_url" {
  description = "EKS OIDC issuer URL."
  value       = local.oidc_issuer_url
}

output "node_group_names" {
  description = "Managed node group names keyed by pool (cpu / gpu)."
  value       = { for k, v in aws_eks_node_group.this : k => v.node_group_name }
}

output "role_arns" {
  description = "IRSA role ARNs keyed by workload: api, control_plane, worker, lb_controller, ebs_csi, vpc_cni, cluster_autoscaler (if enabled)."
  value       = { for k, r in aws_iam_role.irsa : k => r.arn }
}

output "db_endpoint" {
  description = "RDS endpoint host:port."
  value       = aws_db_instance.this.endpoint
}

output "db_address" {
  description = "RDS hostname."
  value       = aws_db_instance.this.address
}

output "db_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}

output "db_master_secret_arn" {
  description = "Secrets Manager ARN of the RDS-managed master credentials (rotated by RDS)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "redis_primary_endpoint" {
  description = "Redis primary endpoint (TLS on port 6379; AUTH token required)."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "redis_reader_endpoint" {
  description = "Redis reader endpoint."
  value       = aws_elasticache_replication_group.this.reader_endpoint_address
}

output "redis_auth_secret_arn" {
  description = "Secrets Manager ARN holding the Redis AUTH token."
  value       = aws_secretsmanager_secret.redis_auth.arn
}

output "bucket_names" {
  description = "S3 bucket names keyed by purpose (models, data)."
  value       = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "bucket_arns" {
  description = "S3 bucket ARNs keyed by purpose."
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}

output "kms_key_arns" {
  description = "KMS key ARNs: data (at rest) and app (envelope encryption)."
  value = {
    data = aws_kms_key.data.arn
    app  = aws_kms_key.app.arn
  }
}

output "alb_security_group_id" {
  description = "Security group to put on the Ingress (alb.ingress.kubernetes.io/security-groups)."
  value       = aws_security_group.alb.id
}

output "waf_web_acl_arn" {
  description = "WAFv2 web ACL ARN for the Ingress annotation alb.ingress.kubernetes.io/wafv2-acl-arn (null when enable_waf = false)."
  value       = var.enable_waf ? aws_wafv2_web_acl.this[0].arn : null
}

output "helm_values" {
  description = "Convenience map shaped for the Tycheon Helm chart values (non-secret)."
  value = {
    cloud  = "aws"
    region = local.region
    cluster = {
      name = aws_eks_cluster.this.name
    }
    serviceAccounts = {
      api          = { name = var.service_account_names.api, roleArn = aws_iam_role.irsa["api"].arn }
      controlPlane = { name = var.service_account_names.control_plane, roleArn = aws_iam_role.irsa["control_plane"].arn }
      worker       = { name = var.service_account_names.worker, roleArn = aws_iam_role.irsa["worker"].arn }
    }
    database = {
      host      = aws_db_instance.this.address
      port      = 5432
      name      = aws_db_instance.this.db_name
      secretArn = aws_db_instance.this.master_user_secret[0].secret_arn
    }
    redis = {
      host      = aws_elasticache_replication_group.this.primary_endpoint_address
      port      = 6379
      tls       = true
      secretArn = aws_secretsmanager_secret.redis_auth.arn
    }
    storage = {
      modelsBucket = aws_s3_bucket.this["models"].bucket
      dataBucket   = aws_s3_bucket.this["data"].bucket
    }
    kms = {
      dataKeyArn = aws_kms_key.data.arn
      appKeyArn  = aws_kms_key.app.arn
    }
    ingress = {
      securityGroupId = aws_security_group.alb.id
      wafAclArn       = var.enable_waf ? aws_wafv2_web_acl.this[0].arn : null
    }
    gpuPool = {
      enabled     = local.gpu_enabled
      nodeLabel   = "tycheon.io/pool=gpu"
      taintKey    = "nvidia.com/gpu"
      taintValue  = "true"
      taintEffect = "NoSchedule"
    }
  }
}
