from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeSender
from wla.agent.app import create_app
from wla.agent.service import ConversationService
from wla.common.settings import Settings


def envelope(data: bytes) -> dict[str, object]:
    return {"message": {"data": base64.b64encode(data).decode(), "messageId": "1"}, "subscription": "s"}


@pytest.fixture
def client(make_service: Callable[..., ConversationService], settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(service=make_service(), settings=settings)) as c:
        yield c


def test_push_processes_message(client: TestClient, sender: FakeSender) -> None:
    event = {
        "message_id": "wamid.X",
        "lead_id": "c" * 32,
        "wa_id": "5511988887777",
        "type": "text",
        "text": "oi",
        "timestamp": 1,
    }
    resp = client.post("/pubsub/push", json=envelope(json.dumps(event).encode()))
    assert resp.status_code == 204
    assert len(sender.sent) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"message": {"data": "%%%não-base64%%%"}},
        envelope(b"{}"),
        envelope(b"not json"),
        {"sem": "message"},
    ],
)
def test_push_acks_permanent_errors(client: TestClient, sender: FakeSender, body: dict[str, object]) -> None:
    assert client.post("/pubsub/push", json=body).status_code == 204
    assert sender.sent == []


def test_push_acks_oversized(client: TestClient, settings: Settings) -> None:
    big = {"message": {"data": "A" * (settings.max_body_bytes * 2 + 10)}}
    assert client.post("/pubsub/push", json=big).status_code == 204


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "DENY"
