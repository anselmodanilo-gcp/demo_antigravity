from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from wla.agent.llm import FALLBACK_REPLY, ChatTurn, GeminiClient, LLMError, _is_transient


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    def generate_content(self, model: str, contents: list[Any], config: Any) -> Any:
        self.requests.append(list(contents))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(responses: list[Any]) -> tuple[GeminiClient, FakeModels]:
    from google.genai import types

    client = object.__new__(GeminiClient)
    models = FakeModels(responses)
    client._types = types
    client._model = "gemini-test"
    client._client = SimpleNamespace(models=models)
    return client, models


def text_response(text: str) -> Any:
    return SimpleNamespace(function_calls=None, text=text, candidates=[])


def call_response(name: str, args: dict[str, Any]) -> Any:
    from google.genai import types

    content = types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)])
    return SimpleNamespace(
        function_calls=[SimpleNamespace(name=name, args=args)],
        text=None,
        candidates=[SimpleNamespace(content=content)],
    )


TOOLS = [{"name": "registrar_resumo", "description": "d", "parameters": {"type": "object", "properties": {}}}]


def test_chat_runs_tool_loop() -> None:
    client, models = make_client(
        [call_response("registrar_resumo", {"resumo": "x"}), text_response(" Olá! ")]
    )
    seen: list[str] = []

    def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        seen.append(name)
        return {"ok": True}

    reply = client.chat("sys", [ChatTurn("user", "oi"), ChatTurn("model", "")], TOOLS, executor, 4)
    assert reply == "Olá!"
    assert seen == ["registrar_resumo"]
    assert len(models.requests[1]) == 3


def test_chat_round_limit_returns_fallback() -> None:
    client, _ = make_client([call_response("registrar_resumo", {}) for _ in range(2)])
    assert client.chat("sys", [ChatTurn("user", "oi")], TOOLS, lambda n, a: {}, 2) == FALLBACK_REPLY


def test_chat_empty_text_returns_fallback() -> None:
    client, _ = make_client([text_response("")])
    assert client.chat("sys", [ChatTurn("user", "oi")], TOOLS, lambda n, a: {}, 2) == FALLBACK_REPLY


def test_chat_wraps_errors() -> None:
    client, _ = make_client([ValueError("400 bad request")])
    with pytest.raises(LLMError):
        client.chat("sys", [ChatTurn("user", "oi")], TOOLS, lambda n, a: {}, 2)


def test_transcribe() -> None:
    client, _ = make_client([text_response(" quero 3 quartos ")])
    assert client.transcribe(b"OggS", "audio/ogg; codecs=opus") == "quero 3 quartos"
    failing, _ = make_client([ValueError("x")])
    with pytest.raises(LLMError):
        failing.transcribe(b"OggS", "audio/ogg")


def test_is_transient() -> None:
    assert _is_transient(SimpleNamespace(code=429))  # type: ignore[arg-type]
    assert _is_transient(TimeoutError())
    assert not _is_transient(ValueError())
