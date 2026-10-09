# ------------------------------------------------------------------ Pub/Sub
resource "google_pubsub_topic" "inbound" {
  name                       = "inbound-messages"
  message_retention_duration = "86400s"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "dlq" {
  name                       = "inbound-dlq"
  message_retention_duration = "604800s"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "events" {
  name = "lead-events"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "agent_push" {
  name                       = "inbound-to-lead-agent"
  topic                      = google_pubsub_topic.inbound.id
  ack_deadline_seconds       = 120
  enable_message_ordering    = true
  message_retention_duration = "86400s"

  push_config {
    push_endpoint = "${module.agent.uri}/pubsub/push"
    oidc_token {
      service_account_email = google_service_account.push.email
      audience              = module.agent.uri
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = 5
  }

  expiration_policy {
    ttl = ""
  }

  depends_on = [google_pubsub_topic_iam_member.dlq_publisher]
}

# Assinatura pull da DLQ: mantém as mensagens para inspeção (e dispara o alerta).
resource "google_pubsub_subscription" "dlq_pull" {
  name                       = "inbound-dlq-inspect"
  topic                      = google_pubsub_topic.dlq.id
  message_retention_duration = "604800s"
  ack_deadline_seconds       = 60

  expiration_policy {
    ttl = ""
  }
}

# Eventos sem PII direto para o BigQuery, sem código (BigQuery subscription).
resource "google_pubsub_subscription" "events_bq" {
  name  = "lead-events-to-bigquery"
  topic = google_pubsub_topic.events.id

  bigquery_config {
    table               = "${var.project_id}.${google_bigquery_table.lead_events.dataset_id}.${google_bigquery_table.lead_events.table_id}"
    use_table_schema    = true
    write_metadata      = true
    drop_unknown_fields = true
  }

  expiration_policy {
    ttl = ""
  }

  depends_on = [google_bigquery_dataset_iam_member.pubsub_writer]
}

# O agente de serviço do Pub/Sub precisa publicar na DLQ e confirmar na assinatura de origem.
resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  topic  = google_pubsub_topic.dlq.id
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent

  depends_on = [google_project_service_identity.pubsub]
}

resource "google_pubsub_subscription_iam_member" "dlq_subscriber" {
  subscription = google_pubsub_subscription.agent_push.id
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

resource "google_pubsub_topic_iam_member" "ingress_publisher" {
  topic  = google_pubsub_topic.inbound.id
  role   = "roles/pubsub.publisher"
  member = google_service_account.ingress.member
}

resource "google_pubsub_topic_iam_member" "dashboard_publisher" {
  topic  = google_pubsub_topic.inbound.id
  role   = "roles/pubsub.publisher"
  member = google_service_account.dashboard.member
}

resource "google_pubsub_topic_iam_member" "agent_events_publisher" {
  topic  = google_pubsub_topic.events.id
  role   = "roles/pubsub.publisher"
  member = google_service_account.agent.member
}

# ------------------------------------------------------------------ BigQuery (analytics sem PII)
resource "google_bigquery_dataset" "analytics" {
  dataset_id                  = "leads_analytics"
  location                    = var.region
  description                 = "Eventos de leads pseudonimizados (sem telefone, nome ou renda exata)."
  default_table_expiration_ms = null
  delete_contents_on_destroy  = true

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_table" "lead_events" {
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "lead_events"
  deletion_protection = false
  description         = "Um registro por interação processada pelo lead-agent."

  time_partitioning {
    type          = "DAY"
    field         = "publish_time"
    expiration_ms = var.retention_days * 24 * 60 * 60 * 1000
  }

  schema = jsonencode([
    { name = "subscription_name", type = "STRING", mode = "NULLABLE" },
    { name = "message_id", type = "STRING", mode = "NULLABLE" },
    { name = "publish_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "attributes", type = "STRING", mode = "NULLABLE" },
    { name = "event_type", type = "STRING", mode = "NULLABLE" },
    { name = "event_time", type = "STRING", mode = "NULLABLE" },
    { name = "lead_id", type = "STRING", mode = "NULLABLE" },
    { name = "channel", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "score", type = "INTEGER", mode = "NULLABLE" },
    { name = "temperature", type = "STRING", mode = "NULLABLE" },
    { name = "tipologia", type = "STRING", mode = "NULLABLE" },
    { name = "faixa_renda", type = "STRING", mode = "NULLABLE" },
    { name = "prazo_compra", type = "STRING", mode = "NULLABLE" },
    { name = "financiamento", type = "STRING", mode = "NULLABLE" },
    { name = "visita_agendada", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "broker_notified", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "message_count", type = "INTEGER", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_dataset_iam_member" "pubsub_writer" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.pubsub_agent

  depends_on = [google_project_service_identity.pubsub]
}
