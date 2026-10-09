"""Persistência: Firestore em produção e implementação em memória para testes."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from wla.agent.domain import Lead

LEADS = "leads"
MESSAGES = "messages"
IDEMPOTENCY = "idempotency"
NOTIFICATIONS = "notifications"


def _now() -> datetime:
    return datetime.now(UTC)


class Repository(Protocol):
    def claim_idempotency(self, key: str, ttl_days: int = 7) -> bool: ...
    def release_idempotency(self, key: str) -> None: ...
    def get_lead(self, lead_id: str) -> Lead | None: ...
    def save_lead(self, lead: Lead, retention_days: int) -> None: ...
    def add_message(
        self, lead_id: str, message_id: str, role: str, text: str, kind: str, retention_days: int
    ) -> bool: ...
    def recent_messages(self, lead_id: str, limit: int) -> list[dict[str, Any]]: ...
    def consume_rate_limit(self, lead_id: str, per_minute: int) -> bool: ...
    def delete_lead(self, lead_id: str) -> None: ...
    def add_notification(self, data: dict[str, Any], retention_days: int) -> None: ...
    def list_leads(self, limit: int) -> list[Lead]: ...
    def list_notifications(self, limit: int) -> list[dict[str, Any]]: ...


class FirestoreRepository:
    def __init__(self, project: str, database: str) -> None:
        from google.cloud import firestore

        self._fs = firestore
        self._db = firestore.Client(project=project, database=database)

    # ---------------------------------------------------------- idempotência
    def claim_idempotency(self, key: str, ttl_days: int = 7) -> bool:
        from google.api_core.exceptions import AlreadyExists

        try:
            self._db.collection(IDEMPOTENCY).document(key).create(
                {"created_at": _now(), "expire_at": _now() + timedelta(days=ttl_days)}
            )
        except AlreadyExists:
            return False
        return True

    def release_idempotency(self, key: str) -> None:
        self._db.collection(IDEMPOTENCY).document(key).delete()

    # ---------------------------------------------------------- leads
    def get_lead(self, lead_id: str) -> Lead | None:
        snap = self._db.collection(LEADS).document(lead_id).get()
        return Lead.model_validate(snap.to_dict()) if snap.exists else None

    def save_lead(self, lead: Lead, retention_days: int) -> None:
        lead.updated_at = _now()
        lead.created_at = lead.created_at or lead.updated_at
        data = lead.model_dump(mode="python")
        data["expire_at"] = _now() + timedelta(days=retention_days)
        self._db.collection(LEADS).document(lead.lead_id).set(data)

    def add_message(
        self, lead_id: str, message_id: str, role: str, text: str, kind: str, retention_days: int
    ) -> bool:
        from google.api_core.exceptions import AlreadyExists

        doc = self._db.collection(LEADS).document(lead_id).collection(MESSAGES).document(message_id)
        try:
            doc.create(
                {
                    "role": role,
                    "text": text,
                    "kind": kind,
                    "ts": _now(),
                    "expire_at": _now() + timedelta(days=retention_days),
                }
            )
        except AlreadyExists:
            return False
        return True

    def recent_messages(self, lead_id: str, limit: int) -> list[dict[str, Any]]:
        q = (
            self._db.collection(LEADS)
            .document(lead_id)
            .collection(MESSAGES)
            .order_by("ts", direction=self._fs.Query.DESCENDING)
            .limit(limit)
        )
        return [(d.to_dict() or {}) | {"id": d.id} for d in q.stream()][::-1]

    def consume_rate_limit(self, lead_id: str, per_minute: int) -> bool:
        """Token bucket transacional por lead (evita abuso e custo excessivo de IA)."""
        ref = self._db.collection("ratelimit").document(lead_id)
        transaction = self._db.transaction()

        @self._fs.transactional
        def _consume(tx: Any) -> bool:
            snap = ref.get(transaction=tx)
            now = _now()
            data: dict[str, Any] = (snap.to_dict() if snap.exists else None) or {
                "tokens": float(per_minute),
                "updated": now,
            }
            updated: datetime = data["updated"]
            elapsed = (now - updated).total_seconds()
            tokens = min(float(per_minute), float(data["tokens"]) + elapsed * per_minute / 60)
            allowed: bool = tokens >= 1
            tx.set(
                ref,
                {
                    "tokens": tokens - 1 if allowed else tokens,
                    "updated": now,
                    "expire_at": now + timedelta(days=1),
                },
            )
            return allowed

        return bool(_consume(transaction))

    def delete_lead(self, lead_id: str) -> None:
        lead_ref = self._db.collection(LEADS).document(lead_id)
        for doc in lead_ref.collection(MESSAGES).stream():
            doc.reference.delete()
        lead_ref.delete()
        self._db.collection("ratelimit").document(lead_id).delete()

    def add_notification(self, data: dict[str, Any], retention_days: int) -> None:
        self._db.collection(NOTIFICATIONS).add(
            data | {"ts": _now(), "expire_at": _now() + timedelta(days=retention_days)}
        )

    def list_leads(self, limit: int) -> list[Lead]:
        q = (
            self._db.collection(LEADS)
            .order_by("updated_at", direction=self._fs.Query.DESCENDING)
            .limit(limit)
        )
        return [Lead.model_validate(d.to_dict()) for d in q.stream()]

    def list_notifications(self, limit: int) -> list[dict[str, Any]]:
        q = (
            self._db.collection(NOTIFICATIONS)
            .order_by("ts", direction=self._fs.Query.DESCENDING)
            .limit(limit)
        )
        return [(d.to_dict() or {}) | {"id": d.id} for d in q.stream()]


class InMemoryRepository:
    """Implementação em memória, com a mesma semântica, para testes e execução local."""

    def __init__(self) -> None:
        self.idempotency: set[str] = set()
        self.leads: dict[str, Lead] = {}
        self.messages: dict[str, dict[str, dict[str, Any]]] = {}
        self.notifications: list[dict[str, Any]] = []
        self.tokens: dict[str, int] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def claim_idempotency(self, key: str, ttl_days: int = 7) -> bool:
        with self._lock:
            if key in self.idempotency:
                return False
            self.idempotency.add(key)
            return True

    def release_idempotency(self, key: str) -> None:
        self.idempotency.discard(key)

    def get_lead(self, lead_id: str) -> Lead | None:
        lead = self.leads.get(lead_id)
        return lead.model_copy(deep=True) if lead else None

    def save_lead(self, lead: Lead, retention_days: int) -> None:
        lead.updated_at = _now()
        lead.created_at = lead.created_at or lead.updated_at
        self.leads[lead.lead_id] = lead.model_copy(deep=True)

    def add_message(
        self, lead_id: str, message_id: str, role: str, text: str, kind: str, retention_days: int
    ) -> bool:
        with self._lock:
            msgs = self.messages.setdefault(lead_id, {})
            if message_id in msgs:
                return False
            self._seq += 1
            msgs[message_id] = {"role": role, "text": text, "kind": kind, "seq": self._seq, "ts": _now()}
            return True

    def recent_messages(self, lead_id: str, limit: int) -> list[dict[str, Any]]:
        msgs = sorted(self.messages.get(lead_id, {}).items(), key=lambda kv: kv[1]["seq"])
        return [v | {"id": k} for k, v in msgs][-limit:]

    def consume_rate_limit(self, lead_id: str, per_minute: int) -> bool:
        used = self.tokens.get(lead_id, 0)
        if used >= per_minute:
            return False
        self.tokens[lead_id] = used + 1
        return True

    def delete_lead(self, lead_id: str) -> None:
        self.leads.pop(lead_id, None)
        self.messages.pop(lead_id, None)

    def add_notification(self, data: dict[str, Any], retention_days: int) -> None:
        self.notifications.append(data | {"ts": _now()})

    def list_leads(self, limit: int) -> list[Lead]:
        leads = sorted(self.leads.values(), key=lambda lead: lead.updated_at or _now(), reverse=True)
        return leads[:limit]

    def list_notifications(self, limit: int) -> list[dict[str, Any]]:
        return list(reversed(self.notifications))[:limit]
