# Workload identities: one Google service account per Tycheon workload, bound to its
# Kubernetes ServiceAccount through Workload Identity, with resource-scoped grants.

locals {
  workloads = {
    api = {
      account_id = "${local.name}-api"
      namespace  = var.k8s_namespace
      ksa        = var.service_account_names.api
      gcs = [
        { bucket = "models", prefix = "", write = false },
        { bucket = "data", prefix = "uploads/", write = true },
      ]
    }
    control_plane = {
      account_id = "${local.name}-cp"
      namespace  = var.k8s_namespace
      ksa        = var.service_account_names.control_plane
      gcs = [
        { bucket = "models", prefix = "registry/", write = true },
        { bucket = "models", prefix = "", write = false },
        { bucket = "data", prefix = "", write = false },
      ]
    }
    worker = {
      account_id = "${local.name}-wrk"
      namespace  = var.k8s_namespace
      ksa        = var.service_account_names.worker
      gcs = [
        { bucket = "models", prefix = "", write = false },
        { bucket = "models", prefix = "runs/", write = true },
        { bucket = "data", prefix = "", write = false },
        { bucket = "data", prefix = "scratch/", write = true },
      ]
    }
  }

  gcs_grants = merge([
    for wl, cfg in local.workloads : {
      for i, g in cfg.gcs : "${wl}-${i}" => merge(g, { workload = wl })
    }
  ]...)

  secrets = {
    db_admin   = google_secret_manager_secret.db_admin.id
    redis_auth = google_secret_manager_secret.redis_auth.id
  }

  workload_secret_grants = merge([
    for wl, cfg in local.workloads : {
      for s, id in local.secrets : "${wl}-${s}" => { workload = wl, secret_id = id }
    }
  ]...)
}

resource "google_service_account" "workload" {
  for_each = local.workloads

  project      = var.project_id
  account_id   = each.value.account_id
  display_name = "Tycheon ${replace(each.key, "_", "-")} (${var.environment})"

  depends_on = [google_project_service.apis]
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = local.workloads

  service_account_id = google_service_account.workload[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.workload_pool}[${each.value.namespace}/${each.value.ksa}]"

  depends_on = [google_container_cluster.this]
}

# Bucket access, scoped by IAM condition to the allowed object prefix.
# Write grants use objectUser (create + overwrite/delete of objects under the prefix).
resource "google_storage_bucket_iam_member" "workload" {
  for_each = local.gcs_grants

  bucket = google_storage_bucket.this[each.value.bucket].name
  role   = each.value.write ? "roles/storage.objectUser" : "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.workload[each.value.workload].email}"

  dynamic "condition" {
    for_each = each.value.prefix == "" ? [] : [each.value.prefix]
    content {
      title      = "prefix-${trimsuffix(condition.value, "/")}"
      expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.this[each.value.bucket].name}/objects/${condition.value}\")"
    }
  }
}

# Application envelope key.
resource "google_kms_crypto_key_iam_member" "workload_app_key" {
  for_each = local.workloads

  crypto_key_id = google_kms_crypto_key.app.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.workload[each.key].email}"
}

resource "google_secret_manager_secret_iam_member" "workload" {
  for_each = local.workload_secret_grants

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workload[each.value.workload].email}"
}

# Cloud SQL: connect only to THIS instance (IAM condition); no project-wide database access.
resource "google_project_iam_member" "workload_cloudsql" {
  for_each = {
    for p in setproduct(keys(local.workloads), ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]) :
    "${p[0]}-${p[1]}" => { workload = p[0], role = p[1] }
  }

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.workload[each.value.workload].email}"

  condition {
    title      = "only-${local.name}-instance"
    expression = "resource.type == \"sqladmin.googleapis.com/Instance\" && resource.name == \"projects/${var.project_id}/instances/${google_sql_database_instance.this.name}\""
  }
}

# IAM database authentication users (Cloud SQL expects the SA email without ".gserviceaccount.com").
resource "google_sql_user" "workload" {
  for_each = local.workloads

  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = trimsuffix(google_service_account.workload[each.key].email, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
