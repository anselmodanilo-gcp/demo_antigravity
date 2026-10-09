# ------------------------------------------------------------------ Firestore (database default = cota gratuita)
resource "google_firestore_database" "default" {
  name                    = "(default)"
  location_id             = var.firestore_location
  type                    = "FIRESTORE_NATIVE"
  concurrency_mode        = "OPTIMISTIC"
  delete_protection_state = "DELETE_PROTECTION_DISABLED"
  deletion_policy         = "DELETE"

  depends_on = [google_project_service.apis]
}

# TTL: o Firestore apaga automaticamente documentos com expire_at no passado (retenção LGPD).
resource "google_firestore_field" "ttl" {
  for_each = toset(local.firestore_ttl)

  database   = google_firestore_database.default.name
  collection = each.value
  field      = "expire_at"

  ttl_config {}

  # Sem índice no campo de TTL (economia de escrita).
  index_config {}
}

# ------------------------------------------------------------------ Secret Manager
# Valores reais da Meta são cadastrados pelo administrador (nova versão); o Terraform cria só
# placeholders, e o app falha fechado enquanto eles existirem.
locals {
  meta_secrets = {
    "wla-meta-app-secret"      = "WLA_META_APP_SECRET"
    "wla-meta-access-token"    = "WLA_META_ACCESS_TOKEN"
    "wla-meta-phone-number-id" = "WLA_META_PHONE_NUMBER_ID"
  }
}

resource "random_password" "placeholder" {
  for_each = local.meta_secrets
  length   = 24
  special  = false
}

resource "random_password" "verify_token" {
  length  = 40
  special = false
}

resource "random_password" "salt" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "meta" {
  for_each  = local.meta_secrets
  secret_id = each.key

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "meta_placeholder" {
  for_each    = local.meta_secrets
  secret      = google_secret_manager_secret.meta[each.key].id
  secret_data = "placeholder-${random_password.placeholder[each.key].result}"

  lifecycle {
    ignore_changes = [secret_data, enabled]
  }
}

resource "google_secret_manager_secret" "verify_token" {
  secret_id = "wla-meta-verify-token"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "verify_token" {
  secret      = google_secret_manager_secret.verify_token.id
  secret_data = random_password.verify_token.result
}

resource "google_secret_manager_secret" "salt" {
  secret_id = "wla-pseudonym-salt"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "salt" {
  secret      = google_secret_manager_secret.salt.id
  secret_data = random_password.salt.result
}

locals {
  secret_ids = merge(
    { for k, v in google_secret_manager_secret.meta : k => v.secret_id },
    {
      "wla-meta-verify-token" = google_secret_manager_secret.verify_token.secret_id
      "wla-pseudonym-salt"    = google_secret_manager_secret.salt.secret_id
    },
  )

  # Quais segredos cada serviço pode ler (menor privilégio).
  secret_access = {
    ingress   = ["wla-meta-app-secret", "wla-meta-verify-token", "wla-meta-phone-number-id", "wla-pseudonym-salt"]
    agent     = ["wla-meta-app-secret", "wla-meta-access-token", "wla-meta-phone-number-id"]
    dashboard = ["wla-pseudonym-salt"]
  }

  secret_env_names = merge(local.meta_secrets, {
    "wla-meta-verify-token" = "WLA_META_VERIFY_TOKEN"
    "wla-pseudonym-salt"    = "WLA_PSEUDONYM_SALT"
  })

  service_accounts = {
    ingress   = google_service_account.ingress.member
    agent     = google_service_account.agent.member
    dashboard = google_service_account.dashboard.member
  }

  secret_bindings = merge([
    for svc, secrets in local.secret_access : {
      for s in secrets : "${svc}/${s}" => { member = local.service_accounts[svc], secret = s }
    }
  ]...)
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = local.secret_bindings
  secret_id = local.secret_ids[each.value.secret]
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member

  depends_on = [
    google_secret_manager_secret_version.meta_placeholder,
    google_secret_manager_secret_version.verify_token,
    google_secret_manager_secret_version.salt,
  ]
}

# ------------------------------------------------------------------ IAM de projeto (mínimo necessário)
resource "google_project_iam_member" "agent_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = google_service_account.agent.member
}

resource "google_project_iam_member" "agent_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.agent.member
}

resource "google_project_iam_member" "dashboard_firestore" {
  project = var.project_id
  role    = "roles/datastore.viewer"
  member  = google_service_account.dashboard.member
}
