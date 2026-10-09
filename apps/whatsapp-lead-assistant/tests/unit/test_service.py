from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.conftest import FakeLLM, FakeSender
from wla.agent.domain import LeadStatus
from wla.agent.service import REPLY_DELETED, REPLY_STOPPED, REPLY_UNSUPPORTED, ConversationService, Outcome
from wla.agent.store import InMemoryRepository
from wla.common.models import Channel, InboundEvent, InboundType
from wla.common.pubsub import InMemoryPublisher

LEAD = "b" * 32
WA = "5511988887777"
Factory = Callable[..., ConversationService]


def event(text: str = "oi", msg_id: str = "wamid.1", **kw: Any) -> InboundEvent:
    data: dict[str, Any] = {
        "message_id": msg_id,
        "lead_id": LEAD,
        "wa_id": WA,
        "profile_name": "Mariana",
        "type": InboundType.TEXT,
        "text": text,
        "timestamp": 1,
    }
    data.update(kw)
    return InboundEvent(**data)


HOT_CALLS: list[tuple[str, dict[str, Any]]] = [
    (
        "atualizar_lead",
        {
            "tipologia": "3Q",
            "renda_familiar_mensal": 20000,
            "prazo_compra": "ate_3_meses",
            "usa_fgts": True,
            "mora_ou_trabalha_na_regiao": True,
        },
    ),
    ("registrar_resumo", {"resumo": "Quer 3Q, renda 20k, compra em 2 meses"}),
]


def test_happy_path_replies_stores_and_publishes(
    make_service: Factory,
    repo: InMemoryRepository,
    sender: FakeSender,
    publisher: InMemoryPublisher,
    llm: FakeLLM,
) -> None:
    result = make_service().handle(event("Oi, vi o anúncio"))
    assert result.outcome == Outcome.REPLIED
    assert sender.sent == [(WA, llm.reply)]
    roles = [m["role"] for m in repo.recent_messages(LEAD, 10)]
    assert roles == ["user", "assistant"]
    lead = repo.get_lead(LEAD)
    assert lead is not None
    assert lead.message_count == 1
    assert lead.profile_name == "Mariana"
    topic, data, _ = publisher.messages[-1]
    assert topic == "lead-events"
    assert data["event_type"] == "lead_updated"
    assert WA not in str(data)
    prompt = llm.calls[0]["system_prompt"]
    assert "Residencial Jardins" in prompt
    assert "{knowledge}" not in prompt
    assert llm.calls[0]["history"][0].role == "user"


def test_duplicate_message_is_ignored(make_service: Factory, sender: FakeSender) -> None:
    svc = make_service()
    svc.handle(event())
    assert svc.handle(event()).outcome == Outcome.DUPLICATE
    assert len(sender.sent) == 1


def test_hot_lead_notifies_broker_once(
    make_service: Factory, llm: FakeLLM, broker: FakeSender, repo: InMemoryRepository
) -> None:
    llm.tool_calls = HOT_CALLS
    svc = make_service()
    first = svc.handle(event("quero 3 quartos", "m1"))
    assert first.temperature == "QUENTE"
    assert first.broker_notified
    llm.tool_calls = [("agendar_visita", {"data_hora": "2026-10-10T10:00:00-03:00"})]
    second = svc.handle(event("sábado 10h", "m2"))
    assert second.temperature == "QUENTE"
    svc.handle(event("obrigada", "m3"))
    assert len(broker.sent) == 1
    to, text = broker.sent[0]
    assert to == "5519999990000"
    assert "QUENTE" in text
    assert "https://painel.example.com" in text
    assert len(repo.notifications) == 1
    lead = repo.get_lead(LEAD)
    assert lead is not None
    assert lead.broker_notified


def test_broker_template_is_used_when_configured(
    make_service: Factory, settings: Any, llm: FakeLLM, broker: FakeSender
) -> None:
    llm.tool_calls = [*HOT_CALLS, ("agendar_visita", {"data_hora": "2026-10-10T10:00:00-03:00"})]
    svc = make_service(settings=settings.model_copy(update={"broker_template_name": "lead_quente"}))
    svc.handle(event("quero comprar"))
    assert broker.templates[0][1] == "lead_quente"
    assert broker.templates[0][2][1] == "***7777"


def test_broker_failure_does_not_break_reply(make_service: Factory, llm: FakeLLM, sender: FakeSender) -> None:
    class Boom(FakeSender):
        def send_text(self, to: str, text: str) -> None:
            raise RuntimeError("graph fora do ar")

    llm.tool_calls = [("transferir_para_corretor", {"motivo": "negociar"})]
    result = make_service(broker_sender=Boom()).handle(event("quero negociar"))
    assert result.outcome == Outcome.REPLIED
    assert result.broker_notified is False
    assert len(sender.sent) == 1


def test_human_phrase_forces_handoff(
    make_service: Factory, repo: InMemoryRepository, broker: FakeSender
) -> None:
    result = make_service().handle(event("Quero falar com um corretor"))
    assert result.broker_notified
    lead = repo.get_lead(LEAD)
    assert lead is not None
    assert lead.status == LeadStatus.ATENDIMENTO_HUMANO
    assert "pediu atendimento humano" in broker.sent[0][1]


def test_delete_my_data(make_service: Factory, repo: InMemoryRepository, sender: FakeSender) -> None:
    svc = make_service()
    svc.handle(event("oi", "m1"))
    result = svc.handle(event("Quero APAGAR meus dados", "m2"))
    assert result.outcome == Outcome.DELETED
    assert repo.get_lead(LEAD) is None
    assert repo.recent_messages(LEAD, 10) == []
    assert sender.sent[-1][1] == REPLY_DELETED


def test_stop_and_reentry(
    make_service: Factory, repo: InMemoryRepository, sender: FakeSender, llm: FakeLLM
) -> None:
    svc = make_service()
    assert svc.handle(event("parar", "m1")).outcome == Outcome.STOPPED
    assert sender.sent[-1][1] == REPLY_STOPPED
    assert svc.handle(event("quanto custa?", "m2")).outcome == Outcome.OPTED_OUT
    assert len(sender.sent) == 1
    assert svc.handle(event("Oi!", "m3")).outcome == Outcome.REPLIED
    lead = repo.get_lead(LEAD)
    assert lead is not None
    assert lead.status != LeadStatus.DESCARTADO


def test_rate_limit(make_service: Factory, sender: FakeSender) -> None:
    svc = make_service()
    outcomes = [svc.handle(event(f"msg {i}", f"m{i}")).outcome for i in range(7)]
    assert outcomes.count(Outcome.RATE_LIMITED) == 2
    assert len(sender.sent) == 5


def test_llm_failure_uses_fallback(make_service: Factory, llm: FakeLLM, sender: FakeSender) -> None:
    llm.fail = True
    result = make_service().handle(event("oi"))
    assert result.outcome == Outcome.REPLIED
    assert "instabilidade" in sender.sent[0][1]


def test_audio_is_transcribed(make_service: Factory, llm: FakeLLM, repo: InMemoryRepository) -> None:
    make_service().handle(event(type=InboundType.AUDIO, text="", media_id="123", mime_type="audio/ogg"))
    first = repo.recent_messages(LEAD, 10)[0]
    assert first["kind"] == "audio"
    assert first["text"] == llm.transcript


def test_audio_failure_falls_back_to_unsupported(
    make_service: Factory, sender: FakeSender, llm: FakeLLM
) -> None:
    sender.media = b""
    make_service().handle(event(type=InboundType.AUDIO, text="", media_id="123"))
    assert sender.sent[-1][1] == REPLY_UNSUPPORTED
    assert llm.calls == []


def test_unsupported_message(make_service: Factory, sender: FakeSender) -> None:
    make_service().handle(event(type=InboundType.UNSUPPORTED, text=""))
    assert sender.sent[-1][1] == REPLY_UNSUPPORTED


def test_permanent_send_error_is_tolerated(
    make_service: Factory, sender: FakeSender, repo: InMemoryRepository
) -> None:
    sender.fail_send = True
    assert make_service().handle(event()).outcome == Outcome.REPLIED
    assert len(repo.recent_messages(LEAD, 10)) == 2


def test_unexpected_error_releases_idempotency(make_service: Factory, repo: InMemoryRepository) -> None:
    class Exploding(FakeLLM):
        def chat(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("bug")

    svc = make_service(llm=Exploding())
    with pytest.raises(RuntimeError):
        svc.handle(event())
    assert "agent-wamid.1" not in repo.idempotency


def test_analytics_publish_failure_is_swallowed(make_service: Factory, publisher: InMemoryPublisher) -> None:
    publisher.fail = True
    assert make_service().handle(event()).outcome == Outcome.REPLIED


def test_simulator_channel_uses_null_sender(
    make_service: Factory, sender: FakeSender, repo: InMemoryRepository
) -> None:
    make_service().handle(event(channel=Channel.SIMULATOR, wa_id="sim-abc12345"))
    assert sender.sent == []
    assert [m["role"] for m in repo.recent_messages(LEAD, 10)] == ["user", "assistant"]


def test_history_starts_with_user(
    make_service: Factory, repo: InMemoryRepository, llm: FakeLLM, settings: Any
) -> None:
    svc = make_service(settings=settings.model_copy(update={"max_history_messages": 2}))
    svc.handle(event("oi", "m1"))
    svc.handle(event("tudo bem?", "m2"))
    history = llm.calls[-1]["history"]
    assert history[0].role == "user"
