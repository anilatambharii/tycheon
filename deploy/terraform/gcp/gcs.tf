# Two buckets mirroring the AWS module: models and data.
# Uniform bucket-level access, public access prevention enforced, CMEK, versioning, lifecycle, soft delete.

locals {
  buckets = {
    models = "model-weights-and-registry"
    data   = "uploads-and-scratch"
  }
}

resource "google_storage_bucket" "this" {
  for_each = local.buckets

  project       = var.project_id
  name          = "${var.project_id}-${local.name}-${each.key}"
  location      = var.region
  storage_class = "STANDARD"
  force_destroy = var.bucket_force_destroy
  labels        = merge(local.labels, { purpose = each.value })

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.data.id
  }

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = var.noncurrent_version_expiration_days
      with_state                 = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_kms_crypto_key_iam_member.data_service_agents,
  ]
}
