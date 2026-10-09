data "google_project" "this" {
  project_id = var.project_id
}

locals {
  labels = {
    app        = "whatsapp-lead-assistant"
    env        = "demo"
    managed-by = "terraform"
  }
  number         = data.google_project.this.number
  pubsub_agent   = "serviceAccount:service-${local.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  iap_agent      = "serviceAccount:service-${local.number}@gcp-sa-iap.iam.gserviceaccount.com"
  dashboard_name = "leads-dashboard"
  firestore_ttl  = ["leads", "messages", "idempotency", "notifications", "ratelimit"]
  common_env = {
    WLA_GCP_PROJECT    = var.project_id
    WLA_REGION         = var.region
    WLA_INBOUND_TOPIC  = google_pubsub_topic.inbound.name
    WLA_EVENTS_TOPIC   = google_pubsub_topic.events.name
    WLA_RETENTION_DAYS = tostring(var.retention_days)
  }
}

# ------------------------------------------------------------------ APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "firestore.googleapis.com",
    "iap.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_service_identity" "iap" {
  provider = google-beta
  service  = "iap.googleapis.com"

  depends_on = [google_project_service.apis]
}

resource "google_project_service_identity" "pubsub" {
  provider = google-beta
  service  = "pubsub.googleapis.com"

  depends_on = [google_project_service.apis]
}

# Artifact Registry é criado no bootstrap (a imagem precisa existir antes do primeiro apply).

# ------------------------------------------------------------------ Service accounts (uma por serviço)
resource "google_service_account" "ingress" {
  account_id   = "sa-wa-ingress"
  display_name = "wa-ingress (webhook público)"
}

resource "google_service_account" "agent" {
  account_id   = "sa-lead-agent"
  display_name = "lead-agent (Gemini + Firestore)"
}

resource "google_service_account" "dashboard" {
  account_id   = "sa-leads-dashboard"
  display_name = "leads-dashboard (painel interno)"
}

resource "google_service_account" "push" {
  account_id   = "sa-pubsub-push"
  display_name = "Identidade OIDC do push do Pub/Sub para o lead-agent"
}
