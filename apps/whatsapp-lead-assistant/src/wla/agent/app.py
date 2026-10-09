"""Serviço `lead-agent`: recebe mensagens do Pub/Sub (push autenticado por OIDC) e responde."""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

from wla.agent.service import ConversationService
from wla.common.http import install_security_headers
from wla.common.logs import configure_logging, set_trace_from_header
from wla.common.models import Channel, InboundEvent
from wla.common.settings import Settings, get_settings

log = logging.getLogger(__name__)


class PushMessage(BaseModel):
    data: str = ""
    messageId: str = ""  # noqa: N815 - formato definido pelo Pub/Sub
    attributes: dict[str, str] = {}


class PushEnvelope(BaseModel):
    message: PushMessage
    subscription: str = ""


def build_service(settings: Settings) -> ConversationService:
    """Monta as dependências reais (GCP). Testes injetam implementações em memória."""
    from wla.agent.llm import GeminiClient
    from wla.agent.store import FirestoreRepository
    from wla.agent.whatsapp import MessageSender, MetaWhatsAppClient, NullSender
    from wla.common.pubsub import PubSubPublisher
    from wla.knowledge import load_knowledge

    meta: MessageSender | None = None
    if settings.meta_configured:
        meta = MetaWhatsAppClient(
            settings.meta_access_token.get_secret_value(),
            settings.meta_phone_number_id,
            settings.meta_graph_version,
            settings.meta_api_base,
        )
    else:
        log.warning("credenciais da Meta não configuradas: respostas do WhatsApp ficam desabilitadas")
    null = NullSender()

    def sender_for(channel: Channel) -> MessageSender:
        return meta if (channel == Channel.WHATSAPP and meta) else null

    return ConversationService(
        settings=settings,
        repo=FirestoreRepository(settings.gcp_project, settings.firestore_database),
        llm=GeminiClient(
            settings.gcp_project,
            settings.gemini_location,
            settings.gemini_model,
            settings.gemini_timeout_seconds,
        ),
        sender_for=sender_for,
        broker_sender=meta,
        publisher=PubSubPublisher(settings.gcp_project),
        kb=load_knowledge(),
    )


def create_app(service: ConversationService | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.gcp_project, settings.log_level)
        if getattr(app.state, "service", None) is None:
            app.state.service = build_service(settings)
        yield

    app = FastAPI(title="lead-agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = service
    install_security_headers(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/pubsub/push")
    async def push(request: Request) -> Response:
        set_trace_from_header(request.headers.get("X-Cloud-Trace-Context"))
        raw = await request.body()
        if len(raw) > settings.max_body_bytes * 2:
            log.error("envelope do Pub/Sub grande demais; descartando")
            return Response(status_code=204)
        try:
            envelope = PushEnvelope.model_validate_json(raw)
            event = InboundEvent.model_validate_json(base64.b64decode(envelope.message.data, validate=True))
        except (ValidationError, ValueError, binascii.Error):
            # Erro permanente: confirma (ack) para não reprocessar indefinidamente.
            log.exception("mensagem inválida descartada")
            return Response(status_code=204)
        result = await run_in_threadpool(app.state.service.handle, event)
        extra: dict[str, Any] = {"lead_id": event.lead_id, "outcome": result.outcome.value}
        log.info("mensagem processada", extra=extra)
        return Response(status_code=204)

    return app
