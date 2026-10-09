# ------------------------------------------------------------------ Alertas (e-mail do administrador)
resource "google_monitoring_notification_channel" "email" {
  display_name = "Admin (e-mail)"
  type         = "email"
  labels = {
    email_address = var.admin_email
  }

  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "dlq" {
  display_name = "WhatsApp leads: mensagens na DLQ"
  combiner     = "OR"

  conditions {
    display_name = "inbound-dlq-inspect com mensagens não entregues"
    condition_threshold {
      filter          = "resource.type = \"pubsub_subscription\" AND resource.labels.subscription_id = \"${google_pubsub_subscription.dlq_pull.name}\" AND metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content   = "Mensagens falharam 5 vezes no lead-agent. Inspecione com: gcloud pubsub subscriptions pull inbound-dlq-inspect --limit=5"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "errors_5xx" {
  display_name = "WhatsApp leads: erros 5xx no Cloud Run"
  combiner     = "OR"

  conditions {
    display_name = "Mais de 5 respostas 5xx em 5 minutos"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = one_of(\"wa-ingress\", \"lead-agent\", \"leads-dashboard\") AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.service_name"]
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}
