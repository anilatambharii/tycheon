# GKE: private regional cluster, Workload Identity, Dataplane V2, CMEK-encrypted secrets,
# one CPU pool and one GPU pool (nvidia-l4, tainted, scale-to-zero).

locals {
  node_pools = merge(
    {
      cpu = {
        machine_type = var.cpu_node_pool.machine_type
        min_nodes    = var.cpu_node_pool.min_nodes
        max_nodes    = var.cpu_node_pool.max_nodes
        initial      = var.cpu_node_pool.min_nodes
        disk_size_gb = var.cpu_node_pool.disk_size_gb
        spot         = var.cpu_node_pool.spot
        policy       = "BALANCED"
        labels       = { "tycheon.io/pool" = "cpu" }
        taints       = []
        accelerator  = null
      }
    },
    local.gpu_enabled ? {
      gpu = {
        machine_type = var.gpu_node_pool.machine_type
        min_nodes    = var.gpu_node_pool.min_nodes
        max_nodes    = var.gpu_node_pool.max_nodes
        initial      = var.gpu_node_pool.min_nodes
        disk_size_gb = var.gpu_node_pool.disk_size_gb
        spot         = var.gpu_node_pool.spot
        policy       = "ANY"
        labels       = { "tycheon.io/pool" = "gpu" }
        taints       = [{ key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }]
        accelerator = {
          type  = var.gpu_node_pool.accelerator_type
          count = var.gpu_node_pool.accelerator_count
        }
      }
    } : {},
  )
}

# Least-privilege node identity (instead of the default Compute SA with Editor).
resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "${local.name}-nodes"
  display_name = "Tycheon GKE nodes (${var.environment})"

  depends_on = [google_project_service.apis]
}

# These roles can only be granted at project level (logging/monitoring/registry have no per-resource scope
# for nodes); they contain no data-plane access to Tycheon data.
resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  project  = var.project_id
  name     = local.name
  location = var.region

  network    = google_compute_network.this.id
  subnetwork = google_compute_subnetwork.nodes.id

  # Node pools are managed separately.
  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode       = "VPC_NATIVE"
  datapath_provider     = "ADVANCED_DATAPATH"
  deletion_protection   = var.deletion_protection
  resource_labels       = local.labels
  enable_shielded_nodes = true

  release_channel {
    channel = var.release_channel
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = var.enable_private_endpoint
    master_ipv4_cidr_block  = var.master_ipv4_cidr
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_networks
      content {
        cidr_block   = cidr_blocks.value.cidr
        display_name = cidr_blocks.value.name
      }
    }
  }

  workload_identity_config {
    workload_pool = local.workload_pool
  }

  # Application-layer encryption of Kubernetes Secrets in etcd with our CMEK.
  database_encryption {
    state    = "ENCRYPTED"
    key_name = google_kms_crypto_key.data.id
  }

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_BASIC"
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS", "APISERVER"]
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus {
      enabled = true
    }
  }

  addons_config {
    http_load_balancing {
      disabled = false
    }
    gce_persistent_disk_csi_driver_config {
      enabled = true
    }
  }

  depends_on = [
    google_project_service.apis,
    google_kms_crypto_key_iam_member.data_service_agents,
    terraform_data.input_guards,
  ]
}

resource "google_container_node_pool" "this" {
  for_each = local.node_pools

  project        = var.project_id
  name           = each.key
  cluster        = google_container_cluster.this.name
  location       = var.region
  node_locations = length(var.zones) > 0 ? var.zones : null

  initial_node_count = each.value.initial

  autoscaling {
    total_min_node_count = each.value.min_nodes
    total_max_node_count = each.value.max_nodes
    location_policy      = each.value.policy
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  node_config {
    machine_type    = each.value.machine_type
    disk_size_gb    = each.value.disk_size_gb
    disk_type       = "pd-balanced"
    image_type      = "COS_CONTAINERD"
    spot            = each.value.spot
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels          = each.value.labels
    resource_labels = local.labels
    tags            = ["${local.name}-gke-node"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    dynamic "taint" {
      for_each = each.value.taints
      content {
        key    = taint.value.key
        value  = taint.value.value
        effect = taint.value.effect
      }
    }

    dynamic "guest_accelerator" {
      for_each = each.value.accelerator == null ? [] : [each.value.accelerator]
      content {
        type  = guest_accelerator.value.type
        count = guest_accelerator.value.count
        gpu_driver_installation_config {
          gpu_driver_version = "DEFAULT"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [initial_node_count]
  }

  depends_on = [google_project_iam_member.nodes]
}
