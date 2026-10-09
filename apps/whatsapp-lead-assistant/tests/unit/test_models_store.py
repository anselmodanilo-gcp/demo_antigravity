from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import load_fixture
from wla.agent.domain import Lead
from wla.agent.store import InMemoryRepository
from wla.agent.whatsapp import NullSender, WhatsAppError
from wla.common.models import InboundType, MetaWebhook
from wla.common.pubsub import InMemoryPublisher


def test_iter_messages_extracts_name_and_phone_id() -> None:
    hook = MetaWebhook.model_validate(load_fixture("text_message.json"))
    [(msg, name, phone_id)] = list(hook.iter_messages())
    assert (msg.from_, name, phone_id) == ("5511988887777", "Mariana", "111111111111111")


def test_iter_messages_ignores_other_objects() -> None:
    data: dict[str, Any] = load_fixture("text_message.json") | {"object": "page"}
    assert list(MetaWebhook.model_validate(data).iter_messages()) == []


def test_strict_types_reject_wrong_shapes() -> None:
    data = load_fixture("text_message.json")
    data["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = 123
    with pytest.raises(ValueError, match="timestamp"):
        MetaWebhook.model_validate(data)


def test_from_meta_button_message() -> None:
    data = load_fixture("text_message.json")
    msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    msg.update({"type": "button", "button": {"text": "Sim", "payload": "x"}})
    msg.pop("text")
    [(m, name, _)] = list(MetaWebhook.model_validate(data).iter_messages())
    from wla.common.models import InboundEvent

    event = InboundEvent.from_meta(m, name, "f" * 32)
    assert (event.type, event.text) == (InboundType.TEXT, "Sim")


def test_in_memory_repository_semantics() -> None:
    repo = InMemoryRepository()
    assert repo.claim_idempotency("k")
    assert not repo.claim_idempotency("k")
    repo.release_idempotency("k")
    assert repo.claim_idempotency("k")
    assert repo.add_message("l", "m1", "user", "oi", "text", 1)
    assert not repo.add_message("l", "m1", "user", "oi", "text", 1)
    lead = Lead(lead_id="l", wa_id="1")
    repo.save_lead(lead, 1)
    assert lead.created_at is not None
    assert repo.list_leads(10)[0].lead_id == "l"
    repo.add_notification({"a": 1}, 1)
    assert repo.list_notifications(10)[0]["a"] == 1
    repo.delete_lead("l")
    assert repo.get_lead("l") is None


def test_in_memory_publisher() -> None:
    pub = InMemoryPublisher()
    assert pub.publish("t", {"a": 1}, "k") == "1"
    pub.fail = True
    with pytest.raises(RuntimeError):
        pub.publish("t", {})


def test_null_sender() -> None:
    s = NullSender()
    s.send_text("1", "oi")
    s.send_template("1", "t", "pt_BR", [])
    with pytest.raises(WhatsAppError):
        s.download_media("1")
