"""Testes de contrato do webhook: payloads no formato real da WhatsApp Cloud API."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import APP_SECRET, VERIFY_TOKEN, load_fixture
from wla.common.models import InboundEvent, InboundType
from wla.common.pubsub import InMemoryPublisher
from wla.common.security import compute_meta_signature, pseudonymize
from wla.common.settings import Settings
from wla.ingress.app import create_app


@pytest.fixture
def client(settings: Settings, publisher: InMemoryPublisher) -> Iterator[TestClient]:
    with TestClient(create_app(publisher=publisher, settings=settings)) as c:
        yield c


def post(
    client: TestClient, payload: dict[str, object] | bytes, secret: str = APP_SECRET, sign: bool = True
) -> httpx.Response:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = compute_meta_signature(body, secret)
    return client.post("/webhook", content=body, headers=headers)


# ------------------------------------------------------------------ verificação (GET)
def test_verify_handshake_ok(client: TestClient) -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"}
    resp = client.get("/webhook", params=params)
    assert resp.status_code == 200
    assert resp.text == "1158201444"
    assert resp.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"}, 403),
        ({"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"}, 403),
        ({"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "<script>"}, 400),
        ({}, 403),
    ],
)
def test_verify_handshake_rejects(client: TestClient, params: dict[str, str], status: int) -> None:
    assert client.get("/webhook", params=params).status_code == status


def test_verify_rejects_placeholder_token(publisher: InMemoryPublisher, settings: Settings) -> None:
    s = settings.model_copy(
        update={"meta_verify_token": settings.meta_verify_token.__class__("placeholder-x")}
    )
    with TestClient(create_app(publisher=publisher, settings=s)) as c:
        params = {"hub.mode": "subscribe", "hub.verify_token": "placeholder-x", "hub.challenge": "1"}
        assert c.get("/webhook", params=params).status_code == 403


# ------------------------------------------------------------------ mensagens (POST)
def test_text_message_is_published(
    client: TestClient, publisher: InMemoryPublisher, settings: Settings
) -> None:
    resp = post(client, load_fixture("text_message.json"))
    assert resp.status_code == 200
    assert len(publisher.messages) == 1
    topic, data, key = publisher.messages[0]
    event = InboundEvent.model_validate(data)
    expected_lead = pseudonymize("5511988887777", settings.pseudonym_salt.get_secret_value())
    assert topic == "inbound-messages"
    assert key == event.lead_id == expected_lead
    assert event.type == InboundType.TEXT
    assert event.text.startswith("Oi, vi o anúncio")
    assert event.profile_name == "Mariana"


def test_audio_message_is_published(client: TestClient, publisher: InMemoryPublisher) -> None:
    assert post(client, load_fixture("audio_message.json")).status_code == 200
    event = InboundEvent.model_validate(publisher.messages[0][1])
    assert event.type == InboundType.AUDIO
    assert event.media_id == "9876543210"


def test_interactive_reply_becomes_text(client: TestClient, publisher: InMemoryPublisher) -> None:
    post(client, load_fixture("interactive_reply.json"))
    assert InboundEvent.model_validate(publisher.messages[0][1]).text == "Quero agendar visita"


def test_image_is_unsupported(client: TestClient, publisher: InMemoryPublisher) -> None:
    post(client, load_fixture("image_message.json"))
    assert InboundEvent.model_validate(publisher.messages[0][1]).type == InboundType.UNSUPPORTED


@pytest.mark.parametrize("fixture", ["status_update.json", "other_number.json"])
def test_events_without_messages_for_us_are_acked(
    client: TestClient, publisher: InMemoryPublisher, fixture: str
) -> None:
    assert post(client, load_fixture(fixture)).status_code == 200
    assert publisher.messages == []


def test_unsigned_or_wrongly_signed_is_rejected(client: TestClient, publisher: InMemoryPublisher) -> None:
    payload = load_fixture("text_message.json")
    assert post(client, payload, sign=False).status_code == 401
    assert post(client, payload, secret="outro-segredo").status_code == 401
    assert publisher.messages == []


def test_signed_but_malformed_is_acked(client: TestClient, publisher: InMemoryPublisher) -> None:
    assert post(client, b'{"object": 123}').status_code == 200
    assert post(client, b"not-json").status_code == 200
    assert publisher.messages == []


def test_oversized_payload_is_rejected(client: TestClient, settings: Settings) -> None:
    big = b"{" + b" " * (settings.max_body_bytes + 10) + b"}"
    assert post(client, big).status_code == 413


def test_fail_closed_without_app_secret(publisher: InMemoryPublisher, settings: Settings) -> None:
    s = settings.model_copy(update={"meta_app_secret": settings.meta_app_secret.__class__("placeholder-y")})
    with TestClient(create_app(publisher=publisher, settings=s)) as c:
        assert post(c, load_fixture("text_message.json"), secret="placeholder-y").status_code == 503


def test_publish_failure_returns_500_for_retry(client: TestClient, publisher: InMemoryPublisher) -> None:
    publisher.fail = True
    assert post(client, load_fixture("text_message.json")).status_code == 500


def test_health_and_security_headers(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.json() == {"status": "ok"}
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert client.get("/docs").status_code == 404
