# Serviço Cloud Run v2 com escala até zero, SA dedicada e segredos do Secret Manager.

resource "google_cloud_run_v2_service" "this" {
  provider = google-beta

  project             = var.project_id
  name                = var.name
  location            = var.region
  ingress             = var.ingress
  deletion_protection = false
  launch_stage        = var.iap_enabled ? "BETA" : null
  iap_enabled         = var.iap_enabled

  invoker_iam_disabled = var.public

  labels = merge(var.labels, { service = var.name })

  template {
    service_account                  = var.service_account_email
    timeout                          = "${var.timeout_seconds}s"
    max_instance_request_concurrency = var.concurrency
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true # cobra CPU só durante requisições
        startup_cpu_boost = true
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "WLA_APP"
        value = var.app
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 0
        period_seconds        = 2
        failure_threshold     = 15
        timeout_seconds       = 2
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invokers)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
