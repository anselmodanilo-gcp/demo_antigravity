"""Orquestração de uma mensagem recebida: idempotência, comandos LGPD, Gemini, resposta e eventos."""

from __future__ import annotations

import logging
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from typing import Any
from zoneinfo import ZoneInfo

from wla.agent.domain import Lead, LeadStatus, income_band, score_lead
from wla.agent.llm import FALLBACK_REPLY, ChatTurn, LLMClient, LLMError
from wla.agent.store import Repository
from wla.agent.tools import ToolContext, execute_tool, tool_declarations
from wla.agent.whatsapp import MessageSender, WhatsAppError
from wla.common.models import Channel, InboundEvent, InboundType
from wla.common.pubsub import Publisher
from wla.common.security import mask_phone
from wla.common.settings import Settings
from wla.knowledge import Knowledge

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/Sao_Paulo")

DELETE_PHRASES = (
    "apagar meus dados",
    "apague meus dados",
    "excluir meus dados",
    "deletar meus dados",
    "remover meus dados",
)
STOP_WORDS = {"parar", "sair", "stop", "cancelar", "descadastrar"}
HUMAN_PHRASES = (
    "falar com corretor",
    "falar com um corretor",
    "falar com humano",
    "falar com uma pessoa",
    "atendente humano",
)

REPLY_DELETED = (
    "Pronto! Apagamos seus dados e o histórico desta conversa. 🗑️ "
    "Se quiser falar com a gente de novo no futuro, é só mandar uma mensagem."
)
REPLY_STOPPED = 'Tudo bem, não enviaremos mais mensagens. Se mudar de ideia, é só mandar um "oi". 👋'
REPLY_UNSUPPORTED = (
    "Por enquanto eu entendo mensagens de texto e áudio. 🙂 Pode me contar por aqui o que procura?"
)


class Outcome(StrEnum):
    REPLIED = "replied"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    OPTED_OUT = "opted_out"
    DELETED = "deleted"
    STOPPED = "stopped"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    temperature: str = ""
    broker_notified: bool = False


def normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def load_system_template() -> str:
    return (resources.files("wla.agent") / "prompts" / "system.md").read_text(encoding="utf-8")


class ConversationService:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        llm: LLMClient,
        sender_for: Callable[[Channel], MessageSender],
        broker_sender: MessageSender | None,
        publisher: Publisher,
        kb: Knowledge,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.s = settings
        self.repo = repo
        self.llm = llm
        self.sender_for = sender_for
        self.broker_sender = broker_sender
        self.publisher = publisher
        self.kb = kb
        self.clock = clock
        self._template = load_system_template()

    # ------------------------------------------------------------------ entrada
    def handle(self, event: InboundEvent) -> Result:
        key = f"agent-{event.message_id}"
        if not self.repo.claim_idempotency(key):
            log.info("mensagem duplicada ignorada", extra={"lead_id": event.lead_id})
            return Result(Outcome.DUPLICATE)
        try:
            return self._process(event)
        except Exception:
            # Libera a chave para que a nova tentativa do Pub/Sub reprocesse a mensagem.
            self.repo.release_idempotency(key)
            raise

    def _process(self, event: InboundEvent) -> Result:
        sender = self.sender_for(event.channel)
        text, kind = self._extract_text(event, sender)
        lead = self.repo.get_lead(event.lead_id) or Lead(
            lead_id=event.lead_id,
            wa_id=event.wa_id,
            channel=event.channel.value,
            profile_name=event.profile_name,
        )
        lead.profile_name = event.profile_name or lead.profile_name
        lead.message_count += 1
        self.repo.add_message(
            lead.lead_id,
            event.message_id,
            "user",
            text or "[mensagem não suportada]",
            kind,
            self.s.retention_days,
        )
        norm = normalize(text)

        if any(p in norm for p in DELETE_PHRASES):
            self.repo.delete_lead(lead.lead_id)
            self._send(sender, lead, REPLY_DELETED, store=False)
            self._publish_event("lead_deleted", lead)
            return Result(Outcome.DELETED)

        if lead.status == LeadStatus.DESCARTADO:
            if norm.strip("!. ") not in {"oi", "ola", "voltar"}:
                self.repo.save_lead(lead, self.s.retention_days)
                return Result(Outcome.OPTED_OUT)
            lead.status = LeadStatus.EM_QUALIFICACAO

        if norm.strip("!. ") in STOP_WORDS:
            lead.status = LeadStatus.DESCARTADO
            self._send(sender, lead, REPLY_STOPPED)
            self.repo.save_lead(lead, self.s.retention_days)
            self._publish_event("lead_stopped", lead)
            return Result(Outcome.STOPPED)

        if not self.repo.consume_rate_limit(lead.lead_id, self.s.rate_limit_per_minute):
            log.warning("limite de mensagens por minuto excedido", extra={"lead_id": lead.lead_id})
            self.repo.save_lead(lead, self.s.retention_days)
            return Result(Outcome.RATE_LIMITED)

        if event.type == InboundType.UNSUPPORTED or not text:
            self._send(sender, lead, REPLY_UNSUPPORTED)
            self.repo.save_lead(lead, self.s.retention_days)
            return Result(Outcome.REPLIED, lead.temperature)

        ctx = ToolContext(lead=lead, kb=self.kb, now=self.clock())
        reply = self._ask_gemini(lead, ctx)
        if any(p in norm for p in HUMAN_PHRASES) and not ctx.handoff_requested:
            execute_tool("transferir_para_corretor", {"motivo": "cliente pediu atendimento humano"}, ctx)

        score, temperature, _ = score_lead(lead.profile, self.kb)
        lead.score, lead.temperature = score, temperature
        lead.notice_sent = True
        self._send(sender, lead, reply)

        notified = False
        if (temperature == "QUENTE" or ctx.handoff_requested) and not lead.broker_notified:
            notified = self._notify_broker(
                lead, "pediu atendimento humano" if ctx.handoff_requested else "lead quente"
            )
            lead.broker_notified = True
        self.repo.save_lead(lead, self.s.retention_days)
        self._publish_event("lead_updated", lead)
        return Result(Outcome.REPLIED, temperature, notified)

    # ------------------------------------------------------------------ etapas
    def _extract_text(self, event: InboundEvent, sender: MessageSender) -> tuple[str, str]:
        if event.type != InboundType.AUDIO:
            return event.text, event.type.value
        try:
            audio, mime = sender.download_media(event.media_id)
            transcript = self.llm.transcribe(audio, event.mime_type or mime)
        except (WhatsAppError, LLMError) as exc:
            log.warning(
                "falha ao transcrever áudio", extra={"lead_id": event.lead_id, "error": type(exc).__name__}
            )
            return "", "audio"
        return transcript, "audio"

    def _ask_gemini(self, lead: Lead, ctx: ToolContext) -> str:
        history = [
            ChatTurn(role="user" if m["role"] == "user" else "model", text=str(m.get("text", "")))
            for m in self.repo.recent_messages(lead.lead_id, self.s.max_history_messages)
            if m["role"] in ("user", "assistant")
        ]
        # O histórico truncado pode começar por uma resposta do modelo; a conversa deve abrir com o usuário.
        while history and history[0].role != "user":
            history.pop(0)
        try:
            return self.llm.chat(
                system_prompt=self._system_prompt(lead),
                history=history,
                tools=tool_declarations(),
                executor=lambda name, args: execute_tool(name, args, ctx),
                max_rounds=self.s.max_tool_rounds,
            )
        except LLMError:
            log.exception("falha no Gemini; usando resposta padrão", extra={"lead_id": lead.lead_id})
            return FALLBACK_REPLY

    def _system_prompt(self, lead: Lead) -> str:
        now = self.clock().astimezone(TZ).strftime("%A, %d/%m/%Y %H:%M")
        profile = lead.profile.model_dump_json(exclude_none=True)
        return (
            self._template.replace("{now}", now)
            .replace("{profile_name}", lead.profile_name or "não informado")
            .replace("{profile}", profile)
            .replace("{knowledge}", self.kb.texto)
        )

    def _send(self, sender: MessageSender, lead: Lead, text: str, store: bool = True) -> None:
        try:
            sender.send_text(lead.wa_id, text)
        except WhatsAppError:
            log.warning(
                "resposta não entregue (erro permanente da Graph API)", extra={"lead_id": lead.lead_id}
            )
        if store:
            self.repo.add_message(
                lead.lead_id, f"out-{uuid.uuid4().hex}", "assistant", text, "text", self.s.retention_days
            )

    def _notify_broker(self, lead: Lead, reason: str) -> bool:
        p = lead.profile
        visita = p.visita.astimezone(TZ).strftime("%d/%m %H:%M") if p.visita else "não agendada"
        lines = [
            f"🔥 Lead {lead.temperature} — {self.kb.empreendimento} ({reason})",
            f"Nome: {p.nome or lead.profile_name or 'não informado'}",
            f"Contato: +{lead.wa_id}" if lead.channel == Channel.WHATSAPP else "Contato: simulador",
            f"Interesse: {p.tipologia or '-'} | Renda: {p.renda_familiar_mensal or '-'}"
            f" | Prazo: {p.prazo_compra or '-'}",
            f"Visita: {visita} | Nota: {lead.score}",
        ]
        if lead.summary:
            lines.append(f"Resumo: {lead.summary}")
        if self.s.dashboard_url:
            lines.append(f"Painel: {self.s.dashboard_url}")
        text = "\n".join(lines)
        self.repo.add_notification(
            {
                "lead_id": lead.lead_id,
                "reason": reason,
                "temperature": lead.temperature,
                "score": lead.score,
                "text": text,
            },
            self.s.retention_days,
        )
        if not (self.broker_sender and self.s.broker_wa_number):
            return True
        try:
            if self.s.broker_template_name:
                self.broker_sender.send_template(
                    self.s.broker_wa_number,
                    self.s.broker_template_name,
                    self.s.broker_template_language,
                    [
                        p.nome or lead.profile_name or "cliente",
                        mask_phone(lead.wa_id),
                        lead.summary or reason,
                    ],
                )
            else:
                self.broker_sender.send_text(self.s.broker_wa_number, text)
        except Exception:
            log.exception("falha ao avisar o corretor pelo WhatsApp", extra={"lead_id": lead.lead_id})
            return False
        return True

    def _publish_event(self, event_type: str, lead: Lead) -> None:
        """Evento sem PII para o BigQuery (id pseudonimizado e renda por faixa)."""
        p = lead.profile
        data: dict[str, Any] = {
            "event_type": event_type,
            "event_time": self.clock().isoformat(),
            "lead_id": lead.lead_id,
            "channel": lead.channel,
            "status": lead.status.value,
            "score": lead.score,
            "temperature": lead.temperature,
            "tipologia": p.tipologia,
            "faixa_renda": income_band(p.renda_familiar_mensal),
            "prazo_compra": p.prazo_compra.value if p.prazo_compra else None,
            "financiamento": p.financiamento.value if p.financiamento else None,
            "visita_agendada": p.visita is not None,
            "broker_notified": lead.broker_notified,
            "message_count": lead.message_count,
        }
        try:
            self.publisher.publish(self.s.events_topic, data)
        except Exception:
            log.exception("falha ao publicar evento de analytics", extra={"lead_id": lead.lead_id})
