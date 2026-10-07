# Cloud SQL PostgreSQL 16 (private IP only, CMEK), Memorystore Redis (TLS + AUTH, CMEK),
# Secret Manager (CMEK) for the generated credentials.

# ---------------------------- Cloud SQL -------------------------------------
resource "google_sql_database_instance" "this" {
  project             = var.project_id
  name                = local.name
  region              = var.region
  database_version    = "POSTGRES_16"
  encryption_key_name = google_kms_crypto_key.data.id
  deletion_protection = var.deletion_protection

  settings {
    tier                        = var.db_tier
    edition                     = var.db_edition
    availability_type           = var.db_availability_type
    disk_type                   = "PD_SSD"
    disk_size                   = var.db_disk_size_gb
    disk_autoresize             = true
    deletion_protection_enabled = var.deletion_protection
    user_labels                 = local.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.this.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = var.db_backup_retention_count
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7
      hour         = 5
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      query_plans_per_minute  = 5
      query_string_length     = 1024
      record_application_tags = false
      record_client_address   = false
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "1000"
    }
  }

  depends_on = [
    google_service_networking_connection.private_service_access,
    google_kms_crypto_key_iam_member.data_service_agents,
  ]
}

resource "google_sql_database" "this" {
  project  = var.project_id
  name     = var.db_name
  instance = google_sql_database_instance.this.name
}

resource "random_password" "db_admin" {
  length  = 40
  special = false
}

# Break-glass admin; day-to-day workloads use IAM database authentication (iam.tf).
resource "google_sql_user" "admin" {
  project  = var.project_id
  name     = "postgres"
  instance = google_sql_database_instance.this.name
  password = random_password.db_admin.result
}

# ---------------------------- Secret Manager (CMEK) -------------------------
resource "google_secret_manager_secret" "db_admin" {
  project   = var.project_id
  secret_id = "${local.name}-db-admin-password"
  labels    = local.labels

  replication {
    user_managed {
      replicas {
        location = var.region
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.data.id
        }
      }
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.data_service_agents]
}

resource "google_secret_manager_secret_version" "db_admin" {
  secret      = google_secret_manager_secret.db_admin.id
  secret_data = random_password.db_admin.result
}

# ---------------------------- Memorystore Redis -----------------------------
resource "google_redis_instance" "this" {
  project        = var.project_id
  name           = local.name
  region         = var.region
  tier           = var.redis_tier
  memory_size_gb = var.redis_memory_size_gb
  redis_version  = var.redis_version
  display_name   = "Tycheon cache (${var.environment})"
  labels         = local.labels

  authorized_network      = google_compute_network.this.id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  customer_managed_key    = google_kms_crypto_key.data.id

  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 4
        minutes = 0
      }
    }
  }

  depends_on = [
    google_service_networking_connection.private_service_access,
    google_kms_crypto_key_iam_member.data_service_agents,
  ]
}

resource "google_secret_manager_secret" "redis_auth" {
  project   = var.project_id
  secret_id = "${local.name}-redis-auth"
  labels    = local.labels

  replication {
    user_managed {
      replicas {
        location = var.region
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.data.id
        }
      }
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.data_service_agents]
}

resource "google_secret_manager_secret_version" "redis_auth" {
  secret      = google_secret_manager_secret.redis_auth.id
  secret_data = google_redis_instance.this.auth_string
}
