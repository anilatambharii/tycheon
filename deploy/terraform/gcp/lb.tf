# Global external HTTPS load balancer prerequisites + Cloud Armor.
#
# The GKE Ingress controller builds the load balancer from an Ingress object. This file creates
# the pieces it consumes:
#   kubernetes.io/ingress.global-static-ip-name: <lb_static_ip_name>
#   ingress.gcp.kubernetes.io/pre-shared-cert:   <lb_managed_certificate_name>
#   networking.gke.io/v1beta1.FrontendConfig:    sslPolicy: <lb_ssl_policy_name>
#   cloud.google.com/v1 BackendConfig:           securityPolicy.name: <armor_policy_name>

resource "google_compute_global_address" "lb" {
  count = var.enable_lb_resources ? 1 : 0

  project      = var.project_id
  name         = "${local.name}-lb"
  address_type = "EXTERNAL"
  ip_version   = "IPV4"
  labels       = local.labels

  depends_on = [google_project_service.apis]
}

resource "google_compute_managed_ssl_certificate" "lb" {
  count = var.enable_lb_resources ? 1 : 0

  project = var.project_id
  name    = "${local.name}-lb"

  managed {
    domains = var.lb_domains
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_ssl_policy" "lb" {
  count = var.enable_lb_resources ? 1 : 0

  project         = var.project_id
  name            = "${local.name}-tls12-restricted"
  profile         = "RESTRICTED"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_security_policy" "armor" {
  count = var.enable_lb_resources ? 1 : 0

  project     = var.project_id
  name        = "${local.name}-edge"
  description = "Tycheon edge protection: preconfigured WAF rules, per-IP throttle, adaptive DDoS defense"
  type        = "CLOUD_ARMOR"

  advanced_options_config {
    json_parsing = "STANDARD"
    log_level    = "VERBOSE"
  }

  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
    }
  }

  rule {
    action      = "throttle"
    priority    = 1000
    description = "Per-IP rate limit"

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = var.armor_rate_limit_per_minute
        interval_sec = 60
      }
    }
  }

  dynamic "rule" {
    for_each = { for i, r in var.armor_waf_rules : r => 2000 + i * 10 }

    content {
      action      = "deny(403)"
      priority    = rule.value
      description = "Preconfigured WAF: ${rule.key}"

      match {
        expr {
          expression = "evaluatePreconfiguredWaf('${rule.key}', {'sensitivity': 1})"
        }
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default rule"

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}
