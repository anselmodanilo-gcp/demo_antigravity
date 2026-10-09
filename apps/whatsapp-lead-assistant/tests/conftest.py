"""Fixtures compartilhadas: dependências em memória e um Gemini simulado (determinístico)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wla.agent.llm import ChatTurn, LLMError, ToolExecutor
from wla.agent.service import ConversationService
from wla.agent.store import InMemoryRepository
from wla.agent.whatsapp import WhatsAppError
from wla.common.models import Channel
from wla.common.pubsub import InMemoryPublisher
from wla.common.settings import Settings
from wla.knowledge import Knowledge, load_knowledge

FIXTURES = Path(__file__).parent / "contract" / "fixtures"
APP_SECRET = "test-app-secret-0123456789"
VERIFY_TOKEN = "test-verify-token-abcdef"
PHONE_NUMBER_ID = "111111111111111"
# Segunda-feira, 10h em Brasília (13h UTC).
FIXED_NOW = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@dataclass
class FakeLLM:
    """Gemini simulado: executa chamadas de ferramenta roteirizadas e devolve uma resposta."""

    reply: str = "Olá! Sou a Clara, do Residencial Jardins. Como posso ajudar?"
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    transcript: str = "quero um apartamento de três quartos"
    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def chat(
        self,
        system_prompt: str,
        history: list[ChatTurn],
        tools: list[dict[str, Any]],
        executor: ToolExecutor,
        max_rounds: int,
    ) -> str:
        self.calls.append({"system_prompt": system_prompt, "history": history, "tools": tools})
        if self.fail:
            raise LLMError("falha simulada")
        for name, args in self.tool_calls:
            self.tool_results.append(executor(name, args))
        return self.reply

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        if self.fail:
            raise LLMError("falha simulada")
        return self.transcript


@dataclass
class FakeSender:
    sent: list[tuple[str, str]] = field(default_factory=list)
    templates: list[tuple[str, str, list[str]]] = field(default_factory=list)
    fail_send: bool = False
    media: bytes = b"OggS-fake-audio"

    def send_text(self, to: str, text: str) -> None:
        if self.fail_send:
            raise WhatsAppError("fora da janela de 24h")
        self.sent.append((to, text))

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None:
        self.templates.append((to, template, params))

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        if not self.media:
            raise WhatsAppError("sem mídia")
        return self.media, "audio/ogg"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gcp_project="test-project",
        meta_app_secret=APP_SECRET,
        meta_verify_token=VERIFY_TOKEN,
        meta_access_token="test-access-token",
        meta_phone_number_id=PHONE_NUMBER_ID,
        pseudonym_salt="test-salt",
        broker_wa_number="5519999990000",
        dashboard_url="https://painel.example.com",
        require_iap=False,
        rate_limit_per_minute=5,
    )


@pytest.fixture
def kb() -> Knowledge:
    return load_knowledge()


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def publisher() -> InMemoryPublisher:
    return InMemoryPublisher()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def broker() -> FakeSender:
    return FakeSender()


@pytest.fixture
def make_service(
    settings: Settings,
    repo: InMemoryRepository,
    llm: FakeLLM,
    sender: FakeSender,
    broker: FakeSender,
    publisher: InMemoryPublisher,
    kb: Knowledge,
) -> Callable[..., ConversationService]:
    def _make(**overrides: Any) -> ConversationService:
        params: dict[str, Any] = {
            "settings": settings,
            "repo": repo,
            "llm": llm,
            "sender_for": lambda channel: sender if channel == Channel.WHATSAPP else FakeSender(),
            "broker_sender": broker,
            "publisher": publisher,
            "kb": kb,
            "clock": lambda: FIXED_NOW,
        }
        params.update(overrides)
        return ConversationService(**params)

    return _make
