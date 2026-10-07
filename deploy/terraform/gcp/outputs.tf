# Outputs mirror the AWS module where a GCP equivalent exists. No secret values are exported.

output "project_id" {
  description = "GCP project ID."
  value       = var.project_id
}

output "region" {
  description = "GCP region."
  value       = var.region
}

output "network_name" {
  description = "VPC network name."
  value       = google_compute_network.this.name
}

output "cluster_name" {
  description = "GKE cluster name."
  value       = google_container_cluster.this.name
}

output "cluster_endpoint" {
  description = "GKE control-plane endpoint."
  value       = var.enable_private_endpoint ? google_container_cluster.this.private_cluster_config[0].private_endpoint : google_container_cluster.this.endpoint
}

output "cluster_ca_certificate" {
  description = "Base64 cluster CA certificate (public)."
  value       = google_container_cluster.this.master_auth[0].cluster_ca_certificate
}

output "workload_identity_pool" {
  description = "Workload Identity pool (project.svc.id.goog) - GCP analogue of the AWS OIDC provider."
  value       = local.workload_pool
}

output "node_pool_names" {
  description = "Node pool names keyed by pool (cpu / gpu)."
  value       = { for k, v in google_container_node_pool.this : k => v.name }
}

output "service_account_emails" {
  description = "Workload Google service account emails keyed by workload (api, control_plane, worker) - annotate the KSAs with iam.gke.io/gcp-service-account."
  value       = { for k, s in google_service_account.workload : k => s.email }
}

output "db_connection_name" {
  description = "Cloud SQL connection name (project:region:instance) for the Cloud SQL Auth Proxy / connector."
  value       = google_sql_database_instance.this.connection_name
}

output "db_private_ip" {
  description = "Cloud SQL private IP address."
  value       = google_sql_database_instance.this.private_ip_address
}

output "db_name" {
  description = "Initial database name."
  value       = google_sql_database.this.name
}

output "db_admin_secret_id" {
  description = "Secret Manager secret ID holding the break-glass postgres admin password."
  value       = google_secret_manager_secret.db_admin.id
}

output "redis_host" {
  description = "Memorystore Redis private host."
  value       = google_redis_instance.this.host
}

output "redis_port" {
  description = "Memorystore Redis port (TLS)."
  value       = google_redis_instance.this.port
}

output "redis_server_ca_certs" {
  description = "Server CA certificates clients must trust for Redis TLS (public certificates)."
  value       = google_redis_instance.this.server_ca_certs[*].cert
}

output "redis_auth_secret_id" {
  description = "Secret Manager secret ID holding the Redis AUTH string."
  value       = google_secret_manager_secret.redis_auth.id
}

output "bucket_names" {
  description = "GCS bucket names keyed by purpose (models, data)."
  value       = { for k, b in google_storage_bucket.this : k => b.name }
}

output "kms_key_ids" {
  description = "Cloud KMS key resource IDs: data (CMEK) and app (envelope encryption)."
  value = {
    data = google_kms_crypto_key.data.id
    app  = google_kms_crypto_key.app.id
  }
}

output "lb_static_ip_name" {
  description = "Global static IP resource name for the Ingress annotation kubernetes.io/ingress.global-static-ip-name (null if disabled)."
  value       = var.enable_lb_resources ? google_compute_global_address.lb[0].name : null
}

output "lb_static_ip_address" {
  description = "Global static IP address - point your DNS A record(s) here (null if disabled)."
  value       = var.enable_lb_resources ? google_compute_global_address.lb[0].address : null
}

output "lb_managed_certificate_name" {
  description = "Managed certificate name for the pre-shared-cert annotation (null if disabled)."
  value       = var.enable_lb_resources ? google_compute_managed_ssl_certificate.lb[0].name : null
}

output "lb_ssl_policy_name" {
  description = "SSL policy name for the FrontendConfig (null if disabled)."
  value       = var.enable_lb_resources ? google_compute_ssl_policy.lb[0].name : null
}

output "armor_policy_name" {
  description = "Cloud Armor policy name for the BackendConfig securityPolicy (null if disabled)."
  value       = var.enable_lb_resources ? google_compute_security_policy.armor[0].name : null
}

output "helm_values" {
  description = "Convenience map shaped for the Tycheon Helm chart values (non-secret)."
  value = {
    cloud   = "gcp"
    project = var.project_id
    region  = var.region
    cluster = {
      name = google_container_cluster.this.name
    }
    serviceAccounts = {
      api          = { name = var.service_account_names.api, gsa = google_service_account.workload["api"].email }
      controlPlane = { name = var.service_account_names.control_plane, gsa = google_service_account.workload["control_plane"].email }
      worker       = { name = var.service_account_names.worker, gsa = google_service_account.workload["worker"].email }
    }
    database = {
      connectionName = google_sql_database_instance.this.connection_name
      privateIp      = google_sql_database_instance.this.private_ip_address
      port           = 5432
      name           = google_sql_database.this.name
      secretId       = google_secret_manager_secret.db_admin.id
    }
    redis = {
      host     = google_redis_instance.this.host
      port     = google_redis_instance.this.port
      tls      = true
      secretId = google_secret_manager_secret.redis_auth.id
    }
    storage = {
      modelsBucket = google_storage_bucket.this["models"].name
      dataBucket   = google_storage_bucket.this["data"].name
    }
    kms = {
      dataKeyId = google_kms_crypto_key.data.id
      appKeyId  = google_kms_crypto_key.app.id
    }
    ingress = {
      staticIpName    = var.enable_lb_resources ? google_compute_global_address.lb[0].name : null
      certificateName = var.enable_lb_resources ? google_compute_managed_ssl_certificate.lb[0].name : null
      sslPolicyName   = var.enable_lb_resources ? google_compute_ssl_policy.lb[0].name : null
      armorPolicyName = var.enable_lb_resources ? google_compute_security_policy.armor[0].name : null
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
