from __future__ import annotations

import json
import logging

import pytest

from wla.common.logs import CloudJsonFormatter, configure_logging, set_trace_from_header, trace_context
from wla.common.security import (
    compute_meta_signature,
    constant_time_equals,
    mask_phone,
    pseudonymize,
    redact_pii,
    verify_meta_signature,
)
from wla.common.settings import Settings


def test_meta_signature_roundtrip() -> None:
    body = b'{"a":1}'
    header = compute_meta_signature(body, "secret")
    assert header.startswith("sha256=")
    assert verify_meta_signature(body, header, "secret")


@pytest.mark.parametrize(
    ("body", "header", "secret"),
    [
        (b"x", None, "secret"),
        (b"x", "", "secret"),
        (b"x", "sha1=abc", "secret"),
        (b"x", compute_meta_signature(b"y", "secret"), "secret"),
        (b"x", compute_meta_signature(b"x", "other"), "secret"),
        (b"x", compute_meta_signature(b"x", "secret"), ""),
    ],
)
def test_meta_signature_rejects(body: bytes, header: str | None, secret: str) -> None:
    assert not verify_meta_signature(body, header, secret)


def test_constant_time_equals() -> None:
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")
    assert not constant_time_equals("", "")


def test_pseudonymize_is_stable_and_normalizes_digits() -> None:
    a = pseudonymize("+55 (11) 98888-7777", "salt")
    assert a == pseudonymize("5511988887777", "salt")
    assert a != pseudonymize("5511988887777", "other-salt")
    assert len(a) == 32
    assert "7777" not in a or a.count("7777") < 2


@pytest.mark.parametrize(("phone", "masked"), [("5511988887777", "***7777"), ("12", "***")])
def test_mask_phone(phone: str, masked: str) -> None:
    assert mask_phone(phone) == masked


def test_redact_pii() -> None:
    text = "me liga no +55 11 98888-7777 ou mande para ana@example.com"
    out = redact_pii(text)
    assert "98888" not in out
    assert "ana@example.com" not in out
    assert "[telefone]" in out
    assert "[email]" in out


def test_settings_meta_configured() -> None:
    ok = Settings(meta_app_secret="s", meta_access_token="t", meta_phone_number_id="1")
    assert ok.meta_configured
    placeholder = Settings(meta_app_secret="placeholder-x", meta_access_token="t", meta_phone_number_id="1")
    assert not placeholder.meta_configured
    assert not Settings().meta_configured


def test_log_formatter_redacts_and_adds_trace() -> None:
    formatter = CloudJsonFormatter("proj")
    record = logging.makeLogRecord(
        {
            "msg": "contato 5511988887777",
            "levelname": "INFO",
            "name": "t",
            "text": "segredo",
            "lead_id": "abc",
        }
    )
    set_trace_from_header("trace123/456;o=1")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        trace_context.set(None)
    assert "5511988887777" not in payload["message"]
    assert payload["text"] == "[redacted]"
    assert payload["lead_id"] == "abc"
    assert payload["logging.googleapis.com/trace"] == "projects/proj/traces/trace123"


def test_log_formatter_includes_redacted_exception() -> None:
    formatter = CloudJsonFormatter("proj")
    try:
        raise ValueError("telefone 5511988887777")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "erro", None, sys.exc_info())
    payload = json.loads(formatter.format(record))
    assert "5511988887777" not in payload["exception"]


def test_configure_logging_sets_json_handler() -> None:
    configure_logging("proj", "debug")
    root = logging.getLogger()
    assert isinstance(root.handlers[0].formatter, CloudJsonFormatter)
    assert root.level == logging.DEBUG
