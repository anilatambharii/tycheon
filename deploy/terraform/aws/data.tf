# Data stores: RDS PostgreSQL 16, ElastiCache Redis, Secrets Manager.
# None is publicly accessible; all are encrypted with the data KMS key.

# ---------------------------- RDS PostgreSQL --------------------------------
resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = local.private_subnet_ids
  tags       = merge(local.tags, { Name = local.name })
}

resource "aws_db_parameter_group" "this" {
  name_prefix = "${local.name}-pg16-"
  family      = "postgres16"
  description = "Tycheon PostgreSQL 16 parameters"

  # Reject non-TLS connections.
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "immediate"
  }
  parameter {
    name         = "log_connections"
    value        = "1"
    apply_method = "immediate"
  }
  parameter {
    name         = "log_disconnections"
    value        = "1"
    apply_method = "immediate"
  }
  parameter {
    name         = "log_min_duration_statement"
    value        = "1000"
    apply_method = "immediate"
  }
  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }

  tags = local.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role" "rds_monitoring" {
  count = var.db_monitoring_interval > 0 ? 1 : 0

  name = "${local.name}-rds-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  count = var.db_monitoring_interval > 0 ? 1 : 0

  role       = aws_iam_role.rds_monitoring[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_db_instance" "this" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = local.db_instance_class

  db_name  = var.db_name
  username = var.db_master_username
  port     = 5432

  # RDS generates and rotates the master password in Secrets Manager (encrypted with the data key).
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.data.arn

  iam_database_authentication_enabled = true

  allocated_storage     = local.db_allocated_storage_gb
  max_allocated_storage = local.db_max_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.data.arn

  multi_az               = local.db_multi_az
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  parameter_group_name   = aws_db_parameter_group.this.name

  backup_retention_period   = local.db_backup_retention_days
  backup_window             = var.db_backup_window
  maintenance_window        = var.db_maintenance_window
  copy_tags_to_snapshot     = true
  delete_automated_backups  = false
  deletion_protection       = local.db_deletion_protection
  skip_final_snapshot       = var.db_skip_final_snapshot
  final_snapshot_identifier = var.db_skip_final_snapshot ? null : "${local.name}-final"

  auto_minor_version_upgrade = true

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.data.arn
  performance_insights_retention_period = var.db_performance_insights_retention_days

  monitoring_interval             = var.db_monitoring_interval
  monitoring_role_arn             = var.db_monitoring_interval > 0 ? aws_iam_role.rds_monitoring[0].arn : null
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = merge(local.tags, { Name = local.name })

  depends_on = [aws_iam_role_policy_attachment.rds_monitoring]
}

# ---------------------------- ElastiCache Redis -----------------------------
resource "random_password" "redis_auth" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "redis_auth" {
  name                    = "${local.name}/redis-auth-token"
  description             = "ElastiCache AUTH token for ${local.name}"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = var.secret_recovery_window_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "redis_auth" {
  secret_id     = aws_secretsmanager_secret.redis_auth.id
  secret_string = random_password.redis_auth.result
}

resource "aws_elasticache_subnet_group" "this" {
  name       = local.name
  subnet_ids = local.private_subnet_ids
  tags       = local.tags
}

resource "aws_elasticache_parameter_group" "this" {
  name        = "${local.name}-redis7"
  family      = "redis7"
  description = "Tycheon Redis 7 parameters"

  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  tags = local.tags
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = local.name
  description          = "Tycheon cache / queue (${var.environment})"

  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = local.redis_node_type
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.this.name

  num_cache_clusters         = local.redis_num_cache_clusters
  automatic_failover_enabled = local.redis_ha
  multi_az_enabled           = local.redis_ha

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  kms_key_id                 = aws_kms_key.data.arn
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  auth_token                 = random_password.redis_auth.result

  snapshot_retention_limit   = var.redis_snapshot_retention_days
  snapshot_window            = "02:00-03:00"
  maintenance_window         = "sun:04:00-sun:05:00"
  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, { Name = local.name })
}
