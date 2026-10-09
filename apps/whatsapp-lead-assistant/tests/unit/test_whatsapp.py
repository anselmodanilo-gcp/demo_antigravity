from __future__ import annotations

import httpx
import pytest
import respx

from wla.agent.whatsapp import MetaWhatsAppClient, WhatsAppError

BASE = "https://graph.example.com/v23.0"


@pytest.fixture
def client() -> MetaWhatsAppClient:
    return MetaWhatsAppClient("tok", "123", "v23.0", "https://graph.example.com")


@respx.mock
def test_send_text_payload(client: MetaWhatsAppClient) -> None:
    route = respx.post(f"{BASE}/123/messages").mock(return_value=httpx.Response(200, json={}))
    client.send_text("5511", "oi")
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer tok"
    assert b'"body":"oi"' in req.content.replace(b" ", b"")


@respx.mock
def test_send_template(client: MetaWhatsAppClient) -> None:
    route = respx.post(f"{BASE}/123/messages").mock(return_value=httpx.Response(200, json={}))
    client.send_template("5511", "lead_quente", "pt_BR", ["Ana"])
    assert b"lead_quente" in route.calls[0].request.content


@respx.mock
def test_permanent_error_is_not_retried(client: MetaWhatsAppClient) -> None:
    route = respx.post(f"{BASE}/123/messages").mock(return_value=httpx.Response(400, json={}))
    with pytest.raises(WhatsAppError):
        client.send_text("5511", "oi")
    assert route.call_count == 1


@respx.mock
def test_download_media_only_from_meta_domains(client: MetaWhatsAppClient) -> None:
    respx.get(f"{BASE}/m1").mock(
        return_value=httpx.Response(
            200, json={"url": "https://lookaside.fbsbx.com/x", "mime_type": "audio/ogg"}
        )
    )
    respx.get("https://lookaside.fbsbx.com/x").mock(return_value=httpx.Response(200, content=b"OggS"))
    assert client.download_media("m1") == (b"OggS", "audio/ogg")

    respx.get(f"{BASE}/m2").mock(return_value=httpx.Response(200, json={"url": "https://evil.example.com/x"}))
    with pytest.raises(WhatsAppError):
        client.download_media("m2")
