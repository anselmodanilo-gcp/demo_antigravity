variable "project_id" {
  type    = string
  default = "advance-mantis-506814-g5"
}

variable "region" {
  description = "Região de todos os recursos (custo menor nos EUA)."
  type        = string
  default     = "us-central1"
}

variable "firestore_location" {
  description = "Local do Firestore (database default = cota gratuita)."
  type        = string
  default     = "nam5"
}

variable "image" {
  description = "Imagem da aplicação fixada por digest. Definida pelo pipeline de deploy."
  type        = string
}

variable "admin_email" {
  description = "Único usuário com acesso ao painel (IAP) e destinatário dos alertas."
  type        = string
  default     = "admin@anselmodanilo.altostrat.com"
}

variable "gemini_model" {
  type    = string
  default = "gemini-2.5-flash"
}

variable "broker_wa_number" {
  description = "WhatsApp do corretor (formato E.164 sem +). Vazio = aviso só no painel."
  type        = string
  default     = ""
}

variable "broker_template_name" {
  description = "Template utilitário aprovado na Meta para avisar o corretor (opcional)."
  type        = string
  default     = ""
}

variable "retention_days" {
  type    = number
  default = 90
}
