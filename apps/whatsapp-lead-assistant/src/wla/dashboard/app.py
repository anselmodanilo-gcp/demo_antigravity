"""Serviço `leads-dashboard`: painel interno + simulador, protegido pelo IAP.

O Cloud Run com IAP habilitado só encaminha requisições autenticadas e autorizadas; ainda
assim o app valida o JWT assinado pelo IAP (`x-goog-iap-jwt-assertion`) — defesa em
profundidade contra acesso que contorne o IAP.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib import resources
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from wla.agent.domain import Lead
from wla.agent.store import Repository
from wla.common.http import install_security_headers
from wla.common.logs import configure_logging
from wla.common.models import Channel, InboundEvent, InboundType
from wla.common.pubsub import Publisher
from wla.common.security import mask_phone, pseudonymize
from wla.common.settings import Settings, get_settings

log = logging.getLogger(__name__)

IAP_HEADER = "x-goog-iap-jwt-assertion"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
LEAD_ID_PATTERN = r"^[a-f0-9]{32}$"
SIM_ID_PATTERN = r"^[A-Za-z0-9_-]{8,40}$"


# ------------------------------------------------------------------ autenticação IAP
class IapVerifier:
    """Valida o JWT do IAP (ES256) com cache das chaves públicas do Google."""

    def __init__(self, audience: str, fetch: Callable[[], dict[str, str]] | None = None, ttl: int = 3600):
        self._audience = audience
        self._fetch = fetch or self._fetch_certs
        self._ttl = ttl
        self._certs: dict[str, str] = {}
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _fetch_certs() -> dict[str, str]:
        import requests

        resp = requests.get(IAP_CERTS_URL, timeout=5)
        resp.raise_for_status()
        data: dict[str, str] = resp.json()
        return data

    def _get_certs(self) -> dict[str, str]:
        with self._lock:
            if not self._certs or time.monotonic() - self._loaded_at > self._ttl:
                self._certs = self._fetch()
                self._loaded_at = time.monotonic()
            return self._certs

    def verify(self, token: str) -> str:
        """Retorna o e-mail autenticado ou levanta ValueError."""
        from google.auth import jwt

        claims: dict[str, Any] = jwt.decode(  # type: ignore[no-untyped-call]
            token, certs=self._get_certs(), audience=self._audience
        )
        if claims.get("iss") != IAP_ISSUER:
            raise ValueError("emissor inválido")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise ValueError("token sem e-mail")
        return email


# ------------------------------------------------------------------ modelos de API
class LeadView(BaseModel):
    lead_id: str
    nome: str
    telefone: str
    canal: str
    status: str
    score: int
    temperatura: str
    tipologia: str | None
    prazo_compra: str | None
    visita: datetime | None
    corretor_avisado: bool
    mensagens: int
    resumo: str
    atualizado_em: datetime | None

    @classmethod
    def from_lead(cls, lead: Lead) -> LeadView:
        p = lead.profile
        return cls(
            lead_id=lead.lead_id,
            nome=p.nome or lead.profile_name or "(sem nome)",
            telefone=mask_phone(lead.wa_id) if lead.channel == Channel.WHATSAPP else "simulador",
            canal=lead.channel,
            status=lead.status.value,
            score=lead.score,
            temperatura=lead.temperature,
            tipologia=p.tipologia,
            prazo_compra=p.prazo_compra.value if p.prazo_compra else None,
            visita=p.visita,
            corretor_avisado=lead.broker_notified,
            mensagens=lead.message_count,
            resumo=lead.summary,
            atualizado_em=lead.updated_at,
        )


class Summary(BaseModel):
    total: int
    por_temperatura: dict[str, int]
    visitas_agendadas: int
    corretor_avisado: int


class MessageView(BaseModel):
    id: str
    role: str
    text: str
    kind: str
    ts: datetime | None


class SimSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=SIM_ID_PATTERN)
    text: str = Field(min_length=1, max_length=1000)
    profile_name: str = Field(default="Visitante (simulador)", max_length=60)


class SimSendResponse(BaseModel):
    lead_id: str
    message_id: str


# ------------------------------------------------------------------ app
def create_app(
    repo: Repository | None = None,
    publisher: Publisher | None = None,
    settings: Settings | None = None,
    verifier: IapVerifier | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.gcp_project, settings.log_level)
        if getattr(app.state, "repo", None) is None:
            from wla.agent.store import FirestoreRepository

            app.state.repo = FirestoreRepository(settings.gcp_project, settings.firestore_database)
        if getattr(app.state, "publisher", None) is None:
            from wla.common.pubsub import PubSubPublisher

            app.state.publisher = PubSubPublisher(settings.gcp_project)
        yield

    app = FastAPI(title="leads-dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.repo = repo
    app.state.publisher = publisher
    install_security_headers(app, csp=DASHBOARD_CSP)
    iap = verifier or (IapVerifier(settings.iap_audience) if settings.require_iap else None)
    if settings.require_iap and not settings.iap_audience and verifier is None:
        raise RuntimeError("WLA_IAP_AUDIENCE é obrigatório quando WLA_REQUIRE_IAP=true")

    def current_user(request: Request) -> str:
        if iap is None:
            return "dev-local"
        token = request.headers.get(IAP_HEADER, "")
        if not token:
            raise HTTPException(status_code=401, detail="não autenticado")
        try:
            return iap.verify(token)
        except Exception:
            log.warning("JWT do IAP inválido")
            raise HTTPException(status_code=401, detail="não autenticado") from None

    def r(request: Request) -> Repository:
        return request.app.state.repo  # type: ignore[no-any-return]

    def static(name: str) -> str:
        return (resources.files("wla.dashboard") / "static" / name).read_text(encoding="utf-8")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(_: str = Depends(current_user)) -> HTMLResponse:
        return HTMLResponse(static("index.html"))

    @app.get("/sim", response_class=HTMLResponse)
    async def simulator(_: str = Depends(current_user)) -> HTMLResponse:
        return HTMLResponse(static("sim.html"))

    @app.get("/static/{name}")
    async def asset(
        name: Literal["app.js", "sim.js", "style.css"], _: str = Depends(current_user)
    ) -> Response:
        media = "text/css" if name.endswith(".css") else "text/javascript"
        return Response(static(name), media_type=media)

    @app.get("/api/me")
    async def me(user: str = Depends(current_user)) -> dict[str, str]:
        return {"email": user}

    @app.get("/api/leads")
    async def leads(request: Request, _: str = Depends(current_user)) -> list[LeadView]:
        items = await run_in_threadpool(r(request).list_leads, 100)
        return [LeadView.from_lead(lead) for lead in items]

    @app.get("/api/summary")
    async def summary(request: Request, _: str = Depends(current_user)) -> Summary:
        items = await run_in_threadpool(r(request).list_leads, 500)
        temps = {"QUENTE": 0, "MORNO": 0, "FRIO": 0}
        for lead in items:
            temps[lead.temperature] = temps.get(lead.temperature, 0) + 1
        return Summary(
            total=len(items),
            por_temperatura=temps,
            visitas_agendadas=sum(1 for lead in items if lead.profile.visita),
            corretor_avisado=sum(1 for lead in items if lead.broker_notified),
        )

    @app.get("/api/leads/{lead_id}/messages")
    async def messages(
        request: Request,
        lead_id: str = Path(pattern=LEAD_ID_PATTERN),
        _: str = Depends(current_user),
    ) -> list[MessageView]:
        items = await run_in_threadpool(r(request).recent_messages, lead_id, 100)
        return [
            MessageView(
                id=str(m.get("id", "")),
                role=str(m.get("role", "")),
                text=str(m.get("text", "")),
                kind=str(m.get("kind", "")),
                ts=m.get("ts"),
            )
            for m in items
        ]

    @app.get("/api/notifications")
    async def notifications(request: Request, _: str = Depends(current_user)) -> list[dict[str, Any]]:
        items = await run_in_threadpool(r(request).list_notifications, 50)
        allowed = {"lead_id", "score", "temperature", "ts", "reason"}
        return [{k: v for k, v in item.items() if k in allowed} for item in items]

    @app.post("/api/sim/send", status_code=202)
    async def sim_send(body: SimSendRequest, user: str = Depends(current_user)) -> SimSendResponse:
        """Publica a mensagem no mesmo tópico do webhook: o fluxo é idêntico ao do WhatsApp."""
        salt = settings.pseudonym_salt.get_secret_value()
        wa_id = f"sim-{body.session_id}"
        lead_id = pseudonymize(wa_id, salt)
        message_id = f"sim.{secrets.token_hex(12)}"
        event = InboundEvent(
            message_id=message_id,
            lead_id=lead_id,
            wa_id=wa_id,
            channel=Channel.SIMULATOR,
            profile_name=body.profile_name,
            type=InboundType.TEXT,
            text=body.text,
            timestamp=int(datetime.now(UTC).timestamp()),
        )
        await run_in_threadpool(
            app.state.publisher.publish, settings.inbound_topic, event.model_dump(mode="json"), lead_id
        )
        log.info("mensagem do simulador publicada", extra={"lead_id": lead_id, "operator": user})
        return SimSendResponse(lead_id=lead_id, message_id=message_id)

    return app
