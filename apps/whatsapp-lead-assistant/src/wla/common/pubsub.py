"""Publicação no Pub/Sub (mensagens recebidas e eventos de analytics)."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, topic: str, data: dict[str, Any], ordering_key: str = "") -> str: ...


class PubSubPublisher:
    def __init__(self, project: str, timeout: float = 10.0) -> None:
        from google.cloud import pubsub_v1  # type: ignore[attr-defined]

        self._project = project
        self._timeout = timeout
        self._client = pubsub_v1.PublisherClient(
            publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
        )

    def publish(self, topic: str, data: dict[str, Any], ordering_key: str = "") -> str:
        path = self._client.topic_path(self._project, topic)
        payload = json.dumps(data, ensure_ascii=False, default=str).encode()
        future = self._client.publish(path, payload, ordering_key=ordering_key)
        try:
            return str(future.result(timeout=self._timeout))
        except Exception:
            if ordering_key:
                # Após erro, a chave fica pausada; retomar permite novas tentativas.
                self._client.resume_publish(path, ordering_key)
            raise


class InMemoryPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any], str]] = []
        self.fail = False

    def publish(self, topic: str, data: dict[str, Any], ordering_key: str = "") -> str:
        if self.fail:
            raise RuntimeError("falha simulada no Pub/Sub")
        self.messages.append((topic, json.loads(json.dumps(data, default=str)), ordering_key))
        return str(len(self.messages))
