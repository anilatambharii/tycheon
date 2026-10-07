terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.20, < 7.0"
    }
    # Only for google_project_service_identity (creates Google-managed service agents; beta-only resource).
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.20, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }
}
