"""Modelos dos webhooks da Meta (WhatsApp Cloud API) e dos eventos internos."""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _MetaModel(BaseModel):
    # Campos novos da Meta são ignorados (compatibilidade futura), mas os tipos são estritos.
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class MetaText(_MetaModel):
    body: str = Field(max_length=4096)


class MetaMedia(_MetaModel):
    id: str = Field(max_length=128)
    mime_type: str = Field(default="application/octet-stream", max_length=128)
    voice: bool = False


class MetaButtonReply(_MetaModel):
    id: str = Field(max_length=256)
    title: str = Field(max_length=256)


class MetaInteractive(_MetaModel):
    type: str
    button_reply: MetaButtonReply | None = None
    list_reply: MetaButtonReply | None = None


class MetaButton(_MetaModel):
    text: str = Field(max_length=256)
    payload: str = Field(default="", max_length=256)


class MetaMessage(_MetaModel):
    from_: str = Field(alias="from", max_length=32)
    id: str = Field(max_length=256)
    timestamp: str = Field(max_length=16)
    type: str = Field(max_length=32)
    text: MetaText | None = None
    audio: MetaMedia | None = None
    interactive: MetaInteractive | None = None
    button: MetaButton | None = None


class MetaProfile(_MetaModel):
    name: str = Field(default="", max_length=256)


class MetaContact(_MetaModel):
    wa_id: str = Field(max_length=32)
    profile: MetaProfile = MetaProfile()


class MetaMetadata(_MetaModel):
    phone_number_id: str = Field(max_length=64)
    display_phone_number: str = Field(default="", max_length=32)


class MetaValue(_MetaModel):
    messaging_product: str = "whatsapp"
    metadata: MetaMetadata | None = None
    contacts: list[MetaContact] = Field(default_factory=list, max_length=50)
    messages: list[MetaMessage] = Field(default_factory=list, max_length=50)


class MetaChange(_MetaModel):
    field: str
    value: MetaValue


class MetaEntry(_MetaModel):
    id: str
    changes: list[MetaChange] = Field(default_factory=list, max_length=50)


class MetaWebhook(_MetaModel):
    object: str
    entry: list[MetaEntry] = Field(default_factory=list, max_length=50)

    def iter_messages(self) -> Iterator[tuple[MetaMessage, str, str]]:
        """Itera (mensagem, nome do contato, phone_number_id) apenas de eventos de mensagem."""
        if self.object != "whatsapp_business_account":
            return
        for entry in self.entry:
            for change in entry.changes:
                if change.field != "messages":
                    continue
                names = {c.wa_id: c.profile.name for c in change.value.contacts}
                phone_id = change.value.metadata.phone_number_id if change.value.metadata else ""
                for msg in change.value.messages:
                    yield msg, names.get(msg.from_, ""), phone_id


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    SIMULATOR = "simulator"


class InboundType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    UNSUPPORTED = "unsupported"


class InboundEvent(BaseModel):
    """Mensagem normalizada publicada em `inbound-messages`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(min_length=1, max_length=256)
    lead_id: str = Field(min_length=8, max_length=64)
    wa_id: str = Field(min_length=1, max_length=64)
    channel: Channel = Channel.WHATSAPP
    profile_name: str = Field(default="", max_length=256)
    type: InboundType
    text: str = Field(default="", max_length=4096)
    media_id: str = Field(default="", max_length=128)
    mime_type: str = Field(default="", max_length=128)
    timestamp: int = Field(ge=0)

    @classmethod
    def from_meta(cls, msg: MetaMessage, profile_name: str, lead_id: str) -> InboundEvent:
        text, media_id, mime = "", "", ""
        kind = InboundType.UNSUPPORTED
        if msg.type == "text" and msg.text:
            kind, text = InboundType.TEXT, msg.text.body
        elif msg.type == "audio" and msg.audio:
            kind, media_id, mime = InboundType.AUDIO, msg.audio.id, msg.audio.mime_type
        elif msg.type == "interactive" and msg.interactive:
            reply = msg.interactive.button_reply or msg.interactive.list_reply
            if reply:
                kind, text = InboundType.TEXT, reply.title
        elif msg.type == "button" and msg.button:
            kind, text = InboundType.TEXT, msg.button.text
        return cls(
            message_id=msg.id,
            lead_id=lead_id,
            wa_id=msg.from_,
            profile_name=profile_name,
            type=kind,
            text=text,
            media_id=media_id,
            mime_type=mime,
            timestamp=int(msg.timestamp) if msg.timestamp.isdigit() else 0,
        )


Temperature = Literal["QUENTE", "MORNO", "FRIO"]
