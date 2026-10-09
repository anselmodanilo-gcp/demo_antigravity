locals {
  secret_env = {
    for svc, secrets in local.secret_access : svc => {
      for s in secrets : local.secret_env_names[s] => local.secret_ids[s]
    }
  }
}

# ------------------------------------------------------------------ wa-ingress (público, autenticado por HMAC)
module "ingress" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = "wa-ingress"
  app                   = "ingress"
  image                 = var.image
  service_account_email = google_service_account.ingress.email
  ingress               = "INGRESS_TRAFFIC_ALL"
  public                = true # allUsers é bloqueado pela org policy; o app valida a assinatura da Meta
  max_instances         = 10
  concurrency           = 80
  memory                = "512Mi"
  timeout_seconds       = 15
  env                   = merge(local.common_env, { WLA_SERVICE_NAME = "wa-ingress" })
  secret_env            = local.secret_env["ingress"]

  depends_on = [google_secret_manager_secret_iam_member.access]
}

# ------------------------------------------------------------------ lead-agent (interno, push OIDC)
module "agent" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = "lead-agent"
  app                   = "agent"
  image                 = var.image
  service_account_email = google_service_account.agent.email
  ingress               = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  invokers              = [google_service_account.push.member]
  max_instances         = 10
  concurrency           = 8
  memory                = "1Gi"
  timeout_seconds       = 120
  env = merge(local.common_env, {
    WLA_SERVICE_NAME         = "lead-agent"
    WLA_GEMINI_MODEL         = var.gemini_model
    WLA_GEMINI_LOCATION      = var.region
    WLA_BROKER_WA_NUMBER     = var.broker_wa_number
    WLA_BROKER_TEMPLATE_NAME = var.broker_template_name
    WLA_DASHBOARD_URL        = module.dashboard.uri
  })
  secret_env = local.secret_env["agent"]

  depends_on = [google_secret_manager_secret_iam_member.access]
}

# ------------------------------------------------------------------ leads-dashboard (IAP)
module "dashboard" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = local.dashboard_name
  app                   = "dashboard"
  image                 = var.image
  service_account_email = google_service_account.dashboard.email
  ingress               = "INGRESS_TRAFFIC_ALL"
  iap_enabled           = true
  invokers              = [local.iap_agent]
  max_instances         = 2
  concurrency           = 40
  memory                = "512Mi"
  timeout_seconds       = 30
  env = merge(local.common_env, {
    WLA_SERVICE_NAME = local.dashboard_name
    WLA_REQUIRE_IAP  = "true"
    WLA_IAP_AUDIENCE = "/projects/${local.number}/locations/${var.region}/services/${local.dashboard_name}"
  })
  secret_env = local.secret_env["dashboard"]

  depends_on = [google_secret_manager_secret_iam_member.access, google_project_service_identity.iap]
}

# Somente o administrador acessa o painel.
resource "google_iap_web_cloud_run_service_iam_member" "admin" {
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = module.dashboard.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "user:${var.admin_email}"
}
