"""Primitivas de segurança: assinatura da Meta, pseudonimização e mascaramento de PII."""

from __future__ import annotations

import hashlib
import hmac
import re

_SIG_PREFIX = "sha256="
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def compute_meta_signature(body: bytes, app_secret: str) -> str:
    """Assinatura no formato do cabeçalho X-Hub-Signature-256 enviado pela Meta."""
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return _SIG_PREFIX + digest


def verify_meta_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """Valida o HMAC-SHA256 do corpo com o App Secret (comparação em tempo constante)."""
    if not header or not app_secret or not header.startswith(_SIG_PREFIX):
        return False
    expected = compute_meta_signature(body, app_secret)
    return hmac.compare_digest(expected, header)


def constant_time_equals(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def pseudonymize(value: str, salt: str) -> str:
    """Identificador estável e não reversível (HMAC-SHA256 com sal secreto)."""
    normalized = re.sub(r"\D", "", value) or value
    return hmac.new(salt.encode(), normalized.encode(), hashlib.sha256).hexdigest()[:32]


def mask_phone(phone: str) -> str:
    """Mantém só os 4 últimos dígitos: +55 11 9****-1234 -> ***1234."""
    digits = re.sub(r"\D", "", phone)
    return "***" + digits[-4:] if len(digits) >= 4 else "***"


def redact_pii(text: str) -> str:
    """Remove telefones e e-mails de textos livres antes de irem para os logs."""
    text = _EMAIL_RE.sub("[email]", text)
    return _PHONE_RE.sub("[telefone]", text)
