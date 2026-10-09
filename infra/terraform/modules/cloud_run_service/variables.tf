variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  description = "Nome do serviço Cloud Run."
  type        = string
}

variable "app" {
  description = "Aplicação dentro da imagem única: ingress, agent ou dashboard."
  type        = string
  validation {
    condition     = contains(["ingress", "agent", "dashboard"], var.app)
    error_message = "app deve ser ingress, agent ou dashboard."
  }
}

variable "image" {
  description = "Imagem fixada por digest (repo@sha256:...)."
  type        = string
}

variable "service_account_email" {
  type = string
}

variable "ingress" {
  type    = string
  default = "INGRESS_TRAFFIC_INTERNAL_ONLY"
}

variable "public" {
  description = "Desliga a checagem de IAM do invoker (endpoint público autenticado pelo próprio app)."
  type        = bool
  default     = false
}

variable "iap_enabled" {
  type    = bool
  default = false
}

variable "invokers" {
  description = "Membros com roles/run.invoker."
  type        = list(string)
  default     = []
}

variable "env" {
  type    = map(string)
  default = {}
}

variable "secret_env" {
  description = "Variável de ambiente => id do secret no Secret Manager."
  type        = map(string)
  default     = {}
}

variable "max_instances" {
  type    = number
  default = 5
}

variable "concurrency" {
  type    = number
  default = 40
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}

variable "timeout_seconds" {
  type    = number
  default = 60
}

variable "labels" {
  type    = map(string)
  default = {}
}
