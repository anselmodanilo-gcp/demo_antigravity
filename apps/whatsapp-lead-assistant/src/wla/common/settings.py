"""Configuração via variáveis de ambiente.

Segredos (App Secret da Meta, tokens e sal de pseudonimização) chegam como variáveis de
ambiente injetadas pelo Cloud Run a partir do Secret Manager; nunca ficam no código.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_PREFIX = "placeholder-"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WLA_", extra="ignore", frozen=True)

    # Ambiente
    gcp_project: str = "local-project"
    region: str = "us-central1"
    service_name: str = "wla"
    log_level: str = "INFO"

    # Pub/Sub e Firestore
    inbound_topic: str = "inbound-messages"
    events_topic: str = "lead-events"
    firestore_database: str = "(default)"

    # Meta / WhatsApp Cloud API
    meta_app_secret: SecretStr = SecretStr("")
    meta_verify_token: SecretStr = SecretStr("")
    meta_access_token: SecretStr = SecretStr("")
    meta_phone_number_id: str = ""
    meta_graph_version: str = "v23.0"
    meta_api_base: str = "https://graph.facebook.com"

    # Segurança e privacidade
    pseudonym_salt: SecretStr = SecretStr("dev-only-salt")
    max_body_bytes: int = Field(default=64_000, ge=1_024, le=1_000_000)
    rate_limit_per_minute: int = Field(default=12, ge=1, le=120)
    retention_days: int = Field(default=90, ge=1, le=365)

    # Gemini (Vertex AI)
    gemini_model: str = "gemini-2.5-flash"
    gemini_location: str = "us-central1"
    gemini_timeout_seconds: float = 30.0
    max_history_messages: int = Field(default=20, ge=2, le=60)
    max_tool_rounds: int = Field(default=4, ge=1, le=8)

    # Corretor (aviso de lead quente)
    broker_wa_number: str = ""
    broker_template_name: str = ""
    broker_template_language: str = "pt_BR"
    dashboard_url: str = ""

    # Painel (IAP)
    iap_audience: str = ""
    require_iap: bool = True

    @property
    def meta_configured(self) -> bool:
        """True quando as credenciais da Meta foram cadastradas (não são placeholders)."""
        secret = self.meta_app_secret.get_secret_value()
        token = self.meta_access_token.get_secret_value()
        return bool(
            secret
            and token
            and self.meta_phone_number_id
            and not secret.startswith(PLACEHOLDER_PREFIX)
            and not token.startswith(PLACEHOLDER_PREFIX)
            and not self.meta_phone_number_id.startswith(PLACEHOLDER_PREFIX)
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
