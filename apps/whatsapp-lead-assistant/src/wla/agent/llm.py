"""Cliente do Gemini (Vertex AI) com laço controlado de function calling."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

log = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
TRANSCRIBE_PROMPT = (
    "Transcreva fielmente este áudio em português do Brasil. Responda apenas com a transcrição."
)
FALLBACK_REPLY = "Desculpe, tive uma instabilidade agora. Pode repetir sua mensagem, por favor?"


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "model"]
    text: str


class LLMError(Exception):
    """Falha ao gerar resposta (transitória ou definitiva)."""


class LLMClient(Protocol):
    def chat(
        self,
        system_prompt: str,
        history: list[ChatTurn],
        tools: list[dict[str, Any]],
        executor: ToolExecutor,
        max_rounds: int,
    ) -> str: ...

    def transcribe(self, audio: bytes, mime_type: str) -> str: ...


def _is_transient(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in (408, 429, 500, 502, 503, 504) or isinstance(exc, TimeoutError | ConnectionError)


class GeminiClient:
    """Implementação com o SDK google-genai em modo Vertex AI (autenticação pela service account)."""

    def __init__(self, project: str, location: str, model: str, timeout_seconds: float = 30.0) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._model = model
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential_jitter(initial=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _generate(self, contents: list[Any], config: Any) -> Any:
        return self._client.models.generate_content(model=self._model, contents=contents, config=config)

    def chat(
        self,
        system_prompt: str,
        history: list[ChatTurn],
        tools: list[dict[str, Any]],
        executor: ToolExecutor,
        max_rounds: int,
    ) -> str:
        t = self._types
        declarations = [
            t.FunctionDeclaration(
                name=d["name"], description=d["description"], parameters_json_schema=d["parameters"]
            )
            for d in tools
        ]
        config = t.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[t.Tool(function_declarations=declarations)],
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.4,
            max_output_tokens=1024,
        )
        contents: list[Any] = [
            t.Content(role=turn.role, parts=[t.Part.from_text(text=turn.text)])
            for turn in history
            if turn.text
        ]
        try:
            for _ in range(max_rounds):
                response = self._generate(contents, config)
                calls = response.function_calls or []
                if not calls:
                    return (response.text or "").strip() or FALLBACK_REPLY
                contents.append(response.candidates[0].content)
                parts = [
                    t.Part.from_function_response(
                        name=call.name, response=executor(call.name, dict(call.args or {}))
                    )
                    for call in calls
                ]
                contents.append(t.Content(role="user", parts=parts))
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        log.warning("limite de rodadas de ferramentas atingido")
        return FALLBACK_REPLY

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        t = self._types
        config = t.GenerateContentConfig(temperature=0.0, max_output_tokens=1024)
        mime = mime_type.split(";", maxsplit=1)[0].strip() or "audio/ogg"
        contents = [
            t.Content(
                role="user",
                parts=[
                    t.Part.from_bytes(data=audio, mime_type=mime),
                    t.Part.from_text(text=TRANSCRIBE_PROMPT),
                ],
            )
        ]
        try:
            response = self._generate(contents, config)
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        return (response.text or "").strip()
