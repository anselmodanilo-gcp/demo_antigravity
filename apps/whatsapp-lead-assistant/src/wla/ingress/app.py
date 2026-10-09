"""Serviço `wa-ingress`: endpoint público do webhook da Meta.

Responsabilidade mínima e rápida (a Meta espera 200 em poucos segundos):
1. valida a assinatura HMAC (X-Hub-Signature-256) com o App Secret;
2. valida o payload com modelos estritos;
3. pseudonimiza o remetente e publica um `InboundEvent` no Pub/Sub (ordenado por lead);
4. responde 200. Todo o processamento com IA acontece de forma assíncrona no `lead-agent`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from wla.common.http import install_security_headers, read_limited_body
from wla.common.logs import configure_logging, set_trace_from_header
from wla.common.models import InboundEvent, MetaWebhook
from wla.common.pubsub import Publisher
from wla.common.security import constant_time_equals, pseudonymize, verify_meta_signature
from wla.common.settings import PLACEHOLDER_PREFIX, Settings, get_settings

log = logging.getLogger(__name__)
MAX_CHALLENGE_LEN = 128


def _usable(secret: str) -> bool:
    return bool(secret) and not secret.startswith(PLACEHOLDER_PREFIX)


def create_app(publisher: Publisher | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.gcp_project, settings.log_level)
        if getattr(app.state, "publisher", None) is None:
            from wla.common.pubsub import PubSubPublisher

            app.state.publisher = PubSubPublisher(settings.gcp_project)
        yield

    app = FastAPI(title="wa-ingress", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.publisher = publisher
    install_security_headers(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/webhook")
    async def verify(
        mode: str = Query(default="", alias="hub.mode", max_length=32),
        token: str = Query(default="", alias="hub.verify_token", max_length=256),
        challenge: str = Query(default="", alias="hub.challenge", max_length=MAX_CHALLENGE_LEN),
    ) -> Response:
        """Handshake de verificação do webhook (feito uma vez ao cadastrar a URL na Meta)."""
        expected = settings.meta_verify_token.get_secret_value()
        if mode != "subscribe" or not _usable(expected) or not constant_time_equals(token, expected):
            log.warning("verificação de webhook recusada")
            raise HTTPException(status_code=403, detail="forbidden")
        if not challenge.isdigit():
            raise HTTPException(status_code=400, detail="challenge inválido")
        return PlainTextResponse(challenge)

    @app.post("/webhook")
    async def receive(request: Request) -> Response:
        set_trace_from_header(request.headers.get("X-Cloud-Trace-Context"))
        body = await read_limited_body(request, settings.max_body_bytes)
        app_secret = settings.meta_app_secret.get_secret_value()
        if not _usable(app_secret):
            # Fail closed: sem App Secret real não há como autenticar a origem.
            log.error("App Secret da Meta não configurado; webhook recusado")
            raise HTTPException(status_code=503, detail="não configurado")
        if not verify_meta_signature(body, request.headers.get("X-Hub-Signature-256"), app_secret):
            log.warning("assinatura inválida no webhook")
            raise HTTPException(status_code=401, detail="assinatura inválida")
        try:
            webhook = MetaWebhook.model_validate_json(body)
        except ValidationError:
            # Assinado pela Meta mas fora do formato esperado: confirma para evitar reenvios.
            log.warning("payload assinado com formato inesperado ignorado")
            return Response(status_code=200)

        salt = settings.pseudonym_salt.get_secret_value()
        published = 0
        for msg, profile_name, phone_id in webhook.iter_messages():
            if settings.meta_phone_number_id and phone_id and phone_id != settings.meta_phone_number_id:
                log.warning("mensagem para outro número ignorada")
                continue
            lead_id = pseudonymize(msg.from_, salt)
            event = InboundEvent.from_meta(msg, profile_name, lead_id)
            try:
                await run_in_threadpool(
                    app.state.publisher.publish,
                    settings.inbound_topic,
                    event.model_dump(mode="json"),
                    lead_id,
                )
            except Exception:
                # 500 faz a Meta reenviar; o lead-agent é idempotente por message_id.
                log.exception("falha ao publicar mensagem recebida", extra={"lead_id": lead_id})
                raise HTTPException(status_code=500, detail="falha temporária") from None
            published += 1
        log.info("webhook recebido", extra={"published": published})
        return Response(status_code=200)

    return app
