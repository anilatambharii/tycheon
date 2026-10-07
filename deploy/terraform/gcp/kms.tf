# Cloud KMS: one key ring, two keys with automatic rotation.
#   data - CMEK for Cloud SQL, Memorystore, GCS, GKE secrets, Secret Manager, (boot disks: see README)
#   app  - application envelope encryption (api / control-plane / worker may use it)
#
# NOTE: key rings and keys can never be deleted in Cloud KMS (only versions are destroyed).
# Recreating an environment with the same name needs a new name or an import.

locals {
  kms_rotation_seconds = "${var.kms_rotation_period_days * 86400}s"
}

resource "google_kms_key_ring" "this" {
  project  = var.project_id
  name     = local.name
  location = var.region

  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "data" {
  name            = "data"
  key_ring        = google_kms_key_ring.this.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = local.kms_rotation_seconds
  labels          = local.labels

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.kms_protection_level
  }
}

resource "google_kms_crypto_key" "app" {
  name            = "app"
  key_ring        = google_kms_key_ring.this.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = local.kms_rotation_seconds
  labels          = local.labels

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.kms_protection_level
  }
}

# Service agents that encrypt/decrypt with the data key on our behalf.
# Service agents for Cloud SQL / Memorystore / Secret Manager are created by Google on first use of
# the API; see README "first apply" note.
resource "google_kms_crypto_key_iam_member" "data_service_agents" {
  for_each = local.service_agents

  crypto_key_id = google_kms_crypto_key.data.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${each.value}"

  depends_on = [google_project_service_identity.agents]
}
