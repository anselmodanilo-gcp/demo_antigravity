"""Cliente da WhatsApp Cloud API (Graph API da Meta) e canais de saída."""

from __future__ import annotations

import logging
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

log = logging.getLogger(__name__)
MAX_MEDIA_BYTES = 16 * 1024 * 1024  # limite da Meta para áudio
# O token só é enviado para domínios da Meta (evita vazar o token via URL manipulada / SSRF).
TRUSTED_MEDIA_DOMAINS = ("fbsbx.com", "facebook.com", "whatsapp.net", "fbcdn.net")


class WhatsAppError(Exception):
    pass


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 500, 502, 503, 504)


class MessageSender(Protocol):
    def send_text(self, to: str, text: str) -> None: ...
    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None: ...
    def download_media(self, media_id: str) -> tuple[bytes, str]: ...


class MetaWhatsAppClient:
    def __init__(
        self, access_token: str, phone_number_id: str, version: str, base_url: str, timeout: float = 10.0
    ):
        self._phone_id = phone_number_id
        self._base = f"{base_url.rstrip('/')}/{version}"
        self._http = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {access_token}"},
            follow_redirects=False,
        )

    @retry(
        retry=retry_if_exception(_transient),
        wait=wait_exponential_jitter(0.5, 8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _post(self, payload: dict[str, Any]) -> None:
        resp = self._http.post(f"{self._base}/{self._phone_id}/messages", json=payload)
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            # Erro permanente (ex.: fora da janela de 24h, número inválido): não adianta repetir.
            raise WhatsAppError(f"Graph API recusou o envio (HTTP {resp.status_code})")
        resp.raise_for_status()

    def send_text(self, to: str, text: str) -> None:
        self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text[:4096]},
            }
        )

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None:
        self._post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": language},
                    "components": [
                        {"type": "body", "parameters": [{"type": "text", "text": p[:1024]} for p in params]}
                    ],
                },
            }
        )

    @retry(
        retry=retry_if_exception(_transient),
        wait=wait_exponential_jitter(0.5, 8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = self._http.get(f"{self._base}/{media_id}")
        meta.raise_for_status()
        info = meta.json()
        url, mime = info.get("url", ""), info.get("mime_type", "audio/ogg")
        host = urlsplit(url).hostname or ""
        trusted = any(host == d or host.endswith("." + d) for d in TRUSTED_MEDIA_DOMAINS)
        if not url.startswith("https://") or not trusted or int(info.get("file_size", 0)) > MAX_MEDIA_BYTES:
            raise WhatsAppError("mídia inválida ou grande demais")
        media = self._http.get(url)
        media.raise_for_status()
        if len(media.content) > MAX_MEDIA_BYTES:
            raise WhatsAppError("mídia grande demais")
        return media.content, mime


class NullSender:
    """Canal do simulador: as mensagens ficam apenas no Firestore e o painel as exibe."""

    def send_text(self, to: str, text: str) -> None:
        log.debug("simulador: mensagem armazenada sem envio")

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None:
        log.debug("simulador: template armazenado sem envio")

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        raise WhatsAppError("simulador não suporta mídia")
