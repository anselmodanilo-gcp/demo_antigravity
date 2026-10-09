from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from wla.agent.domain import Lead, LeadProfile
from wla.agent.store import InMemoryRepository
from wla.common.models import Channel, InboundEvent
from wla.common.pubsub import InMemoryPublisher
from wla.common.settings import Settings
from wla.dashboard.app import IAP_HEADER, IAP_ISSUER, IapVerifier, create_app

AUDIENCE = "/projects/123/locations/us-central1/services/leads-dashboard"


def _keypair() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


PRIVATE, PUBLIC = _keypair()


def make_token(
    audience: str = AUDIENCE, issuer: str = IAP_ISSUER, email: str | None = "admin@example.com"
) -> str:
    from google.auth import crypt, jwt

    signer = crypt.ES256Signer.from_string(PRIVATE, "kid-1")
    now = int(time.time())
    payload: dict[str, object] = {"aud": audience, "iss": issuer, "iat": now, "exp": now + 300, "sub": "1"}
    if email:
        payload["email"] = email
    return jwt.encode(signer, payload).decode()


@pytest.fixture
def verifier() -> IapVerifier:
    return IapVerifier(AUDIENCE, fetch=lambda: {"kid-1": PUBLIC})


@pytest.fixture
def client(
    repo: InMemoryRepository, publisher: InMemoryPublisher, settings: Settings, verifier: IapVerifier
) -> Iterator[TestClient]:
    s = settings.model_copy(update={"require_iap": True, "iap_audience": AUDIENCE})
    app = create_app(repo=repo, publisher=publisher, settings=s, verifier=verifier)
    with TestClient(app, headers={IAP_HEADER: make_token()}) as c:
        yield c


def _seed(repo: InMemoryRepository) -> None:
    hot = Lead(
        lead_id="d" * 32, wa_id="5511988887777", profile_name="Mariana", score=85, temperature="QUENTE"
    )
    hot.profile = LeadProfile(nome="Mariana", tipologia="3Q")
    hot.broker_notified = True
    sim = Lead(lead_id="e" * 32, wa_id="sim-abc", channel=Channel.SIMULATOR.value, score=10)
    repo.save_lead(hot, 90)
    repo.save_lead(sim, 90)
    repo.add_message("d" * 32, "m1", "user", "oi", "text", 90)
    repo.add_notification(
        {
            "lead_id": "d" * 32,
            "reason": "lead quente",
            "temperature": "QUENTE",
            "score": 85,
            "text": "Contato: +5511988887777",
        },
        90,
    )


# ------------------------------------------------------------------ autenticação
@pytest.mark.parametrize(
    "token",
    ["", "lixo", make_token(audience="/outro"), make_token(issuer="https://evil"), make_token(email=None)],
)
def test_rejects_missing_or_invalid_iap_token(client: TestClient, token: str) -> None:
    resp = client.get("/api/leads", headers={IAP_HEADER: token})
    assert resp.status_code == 401


def test_requires_audience_when_iap_enabled(settings: Settings) -> None:
    with pytest.raises(RuntimeError):
        create_app(settings=settings.model_copy(update={"require_iap": True, "iap_audience": ""}))


def test_me_returns_email(client: TestClient) -> None:
    assert client.get("/api/me").json() == {"email": "admin@example.com"}


def test_verifier_caches_certs() -> None:
    calls: list[int] = []

    def fetch() -> dict[str, str]:
        calls.append(1)
        return {"kid-1": PUBLIC}

    v = IapVerifier(AUDIENCE, fetch=fetch)
    v.verify(make_token())
    v.verify(make_token())
    assert len(calls) == 1


# ------------------------------------------------------------------ API
def test_leads_are_masked(client: TestClient, repo: InMemoryRepository) -> None:
    _seed(repo)
    leads = client.get("/api/leads").json()
    assert {lead["telefone"] for lead in leads} == {"***7777", "simulador"}
    assert "5511988887777" not in str(leads)


def test_summary(client: TestClient, repo: InMemoryRepository) -> None:
    _seed(repo)
    s = client.get("/api/summary").json()
    assert s["total"] == 2
    assert s["por_temperatura"]["QUENTE"] == 1
    assert s["corretor_avisado"] == 1


def test_messages_validate_lead_id(client: TestClient, repo: InMemoryRepository) -> None:
    _seed(repo)
    assert client.get(f"/api/leads/{'d' * 32}/messages").json()[0]["text"] == "oi"
    assert client.get("/api/leads/..%2F..%2Fetc/messages").status_code in (404, 422)
    assert client.get("/api/leads/XYZ/messages").status_code == 422


def test_notifications_hide_phone(client: TestClient, repo: InMemoryRepository) -> None:
    _seed(repo)
    notes = client.get("/api/notifications").json()
    assert notes[0]["reason"] == "lead quente"
    assert "text" not in notes[0]


def test_simulator_publishes_inbound_event(client: TestClient, publisher: InMemoryPublisher) -> None:
    resp = client.post("/api/sim/send", json={"session_id": "abcdef123456", "text": "oi"})
    assert resp.status_code == 202
    topic, data, key = publisher.messages[0]
    event = InboundEvent.model_validate(data)
    assert topic == "inbound-messages"
    assert event.channel == Channel.SIMULATOR
    assert key == event.lead_id == resp.json()["lead_id"]


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "x", "text": "oi"},
        {"session_id": "abcdef123456", "text": ""},
        {"session_id": "abcdef123456", "text": "a" * 1001},
        {"session_id": "abcdef123456", "text": "oi", "extra": 1},
    ],
)
def test_simulator_validates_input(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/api/sim/send", json=body).status_code == 422


def test_pages_and_assets(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert "script-src 'self'" in home.headers["content-security-policy"]
    assert client.get("/sim").status_code == 200
    assert client.get("/static/app.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/static/style.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/../app.py").status_code == 404
    assert client.get("/static/secret.txt").status_code == 422


def test_dev_mode_without_iap(
    repo: InMemoryRepository, publisher: InMemoryPublisher, settings: Settings
) -> None:
    with TestClient(create_app(repo=repo, publisher=publisher, settings=settings)) as c:
        assert c.get("/api/me").json() == {"email": "dev-local"}
        assert c.get("/healthz").json() == {"status": "ok"}
