# Tycheon on GCP - reusable module. It does NOT configure the google provider;
# the caller supplies it (see examples/complete). No data source here calls a
# Google API, so `terraform plan` needs no lookups (project number is a variable).

locals {
  name = "${var.name}-${var.environment}"

  labels = merge(
    {
      application   = "tycheon"
      environment   = var.environment
      managed_by    = "terraform"
      tycheon_cloud = "gcp"
    },
    var.labels,
  )

  required_apis = [
    "compute.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "redis.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudkms.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "storage.googleapis.com",
  ]

  workload_pool = "${var.project_id}.svc.id.goog"

  # Google-managed service agents that must be able to use the CMEK key.
  # These addresses are deterministic from the project number.
  service_agents = {
    cloudsql      = "service-${var.project_number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"
    redis         = "service-${var.project_number}@cloud-redis.iam.gserviceaccount.com"
    gcs           = "service-${var.project_number}@gs-project-accounts.iam.gserviceaccount.com"
    gke           = "service-${var.project_number}@container-engine-robot.iam.gserviceaccount.com"
    secretmanager = "service-${var.project_number}@gcp-sa-secretmanager.iam.gserviceaccount.com"
  }

  gpu_enabled = var.gpu_node_pool.enabled
}

resource "google_project_service" "apis" {
  for_each = var.enable_apis ? toset(local.required_apis) : toset([])

  project                    = var.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

# Make sure the Google-managed service agents exist before they are granted CMEK access.
resource "google_project_service_identity" "agents" {
  for_each = var.enable_apis ? {
    cloudsql      = "sqladmin.googleapis.com"
    redis         = "redis.googleapis.com"
    secretmanager = "secretmanager.googleapis.com"
  } : {}

  # Beta-only resource: the caller must pass a google-beta provider (see examples/complete).
  provider = google-beta
  project  = var.project_id
  service  = each.value

  depends_on = [google_project_service.apis]
}

# Cross-variable guards evaluated at plan time.
resource "terraform_data" "input_guards" {
  input = local.name

  lifecycle {
    precondition {
      condition     = !var.enable_lb_resources || length(var.lb_domains) > 0
      error_message = "lb_domains must be set when enable_lb_resources = true (managed certificate needs at least one domain)."
    }
  }
}
