output "webhook_url" {
  description = "URL para cadastrar no painel da Meta (Callback URL)."
  value       = "${module.ingress.uri}/webhook"
}

output "dashboard_url" {
  value = module.dashboard.uri
}

output "agent_url" {
  value = module.agent.uri
}

output "verify_token_secret" {
  description = "Leia o Verify Token com: gcloud secrets versions access latest --secret=<este valor>"
  value       = google_secret_manager_secret.verify_token.secret_id
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/demo-antigravity"
}
