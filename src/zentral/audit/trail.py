"""Tamper-evident audit trail.

Every security-relevant action — including *reads* of personal data and
*denied* attempts — is recorded as an ``AuditEvent``. Events form a hash chain:

    hash_n = HMAC-SHA256(key, hash_{n-1} || canonical_json(event_n))

Altering, removing or reordering any stored event breaks the chain from that
point on, which ``verify`` detects. Because the chain is keyed, someone with
write access to the database but without the application's audit key cannot
forge a consistent chain.

Events are written in the *same database transaction* as the change they
describe: an action cannot commit without its audit record, and vice versa.

Audit ``details`` must never contain personal data or message content — only
identifiers, field names and enumerated values.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zentral.db.models import AuditCheckpoint, AuditEvent, utcnow
from zentral.security.principal import Principal

GENESIS_HASH = "0" * 64
_ADVISORY_LOCK_KEY = 0x5A454E54  # "ZENT"

audit_log = structlog.get_logger("zentral.audit")


@dataclass(frozen=True, slots=True)
class RequestMeta:
    channel: str  # api | mcp | hook | worker | cli
    request_id: str = ""
    ip: str = ""


@dataclass(slots=True)
class PendingEvent:
    action: str
    resource_type: str
    resource_id: str
    outcome: str = "success"
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    # Events up to here were archived and removed; the chain resumes after it.
    archived_through: int = 0
    reason: str = ""


def _canonical(event: AuditEvent) -> bytes:
    payload = {
        "seq": event.seq,
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "actor_id": event.actor_id,
        "actor_kind": event.actor_kind,
        "actor_roles": sorted(event.actor_roles),
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "channel": event.channel,
        "request_id": event.request_id,
        "ip": event.ip,
        "details": event.details,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class AuditTrail:
    def __init__(self, hmac_key: str, previous_keys: tuple[str, ...] = ()) -> None:
        """``previous_keys`` are retired keys that are still accepted when
        *verifying* events written before a key rotation. New events are always
        written with ``hmac_key``."""
        if not hmac_key:
            raise ValueError("audit HMAC key is required")
        self._key = hmac_key.encode()
        self._verify_keys = (self._key, *(k.encode() for k in previous_keys if k))
        self._local_lock = asyncio.Lock()

    def _digest(self, prev_hash: str, event: AuditEvent, key: bytes | None = None) -> str:
        message = prev_hash.encode() + _canonical(event)
        return hmac.new(key or self._key, message, hashlib.sha256).hexdigest()

    def matches(self, prev_hash: str, event: AuditEvent) -> bool:
        """Whether ``event`` is correctly chained to ``prev_hash`` (any known key)."""
        return self._matches(prev_hash, event)

    def sign(self, payload: dict[str, Any]) -> str:
        """HMAC of a canonical JSON document with the current audit key."""
        message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def signed_by_us(self, payload: dict[str, Any], signature: str) -> bool:
        message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return any(
            hmac.compare_digest(signature, hmac.new(k, message, hashlib.sha256).hexdigest())
            for k in self._verify_keys
        )

    @staticmethod
    def checkpoint_payload(cp: AuditCheckpoint) -> dict[str, Any]:
        return {
            "kind": "zentral-audit-checkpoint/1",
            "seq": cp.seq,
            "hash": cp.hash,
            "first_seq": cp.first_seq,
            "events": cp.events,
            "archive_sha256": cp.archive_sha256,
            "created_by": cp.created_by,
            "prev_signature": cp.prev_signature,
        }

    async def checkpoints(self, session: AsyncSession) -> list[AuditCheckpoint]:
        return list(
            (await session.scalars(select(AuditCheckpoint).order_by(AuditCheckpoint.seq))).all()
        )

    def _matches(self, prev_hash: str, event: AuditEvent) -> bool:
        return any(
            hmac.compare_digest(event.hash, self._digest(prev_hash, event, key))
            for key in self._verify_keys
        )

    @asynccontextmanager
    async def serialized(self, session: AsyncSession) -> AsyncIterator[None]:
        """Serialise chain appends until the surrounding transaction ends.

        PostgreSQL: a transaction-scoped advisory lock (works across processes).
        Other backends (SQLite, development only): an in-process lock.
        """
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
            )
            yield
        else:
            async with self._local_lock:
                yield

    async def write(
        self,
        session: AsyncSession,
        events: list[PendingEvent],
        actor: Principal,
        meta: RequestMeta,
    ) -> None:
        """Append events to the chain. Must be called inside ``serialized``."""
        if not events:
            return
        last = (
            await session.execute(
                select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
            )
        ).first()
        if last is None:
            # Everything so far may have been archived: continue after the checkpoint.
            checkpoint = (
                await session.execute(
                    select(AuditCheckpoint.seq, AuditCheckpoint.hash)
                    .order_by(AuditCheckpoint.seq.desc())
                    .limit(1)
                )
            ).first()
        seq, prev_hash = (
            (last.seq, last.hash)
            if last
            else (checkpoint.seq, checkpoint.hash)
            if checkpoint
            else (0, GENESIS_HASH)
        )
        for pending in events:
            seq += 1
            event = AuditEvent(
                seq=seq,
                id=uuid.uuid4(),
                occurred_at=pending.occurred_at,
                actor_id=actor.subject,
                actor_kind=actor.kind,
                actor_roles=sorted(actor.roles),
                action=pending.action,
                resource_type=pending.resource_type,
                resource_id=pending.resource_id,
                outcome=pending.outcome,
                channel=meta.channel,
                request_id=meta.request_id,
                ip=meta.ip,
                details=pending.details,
                prev_hash=prev_hash,
                hash="",
            )
            event.hash = self._digest(prev_hash, event)
            prev_hash = event.hash
            session.add(event)
            # Mirror to the structured log so events can be shipped to a SIEM
            # in near real time, independent of the database.
            audit_log.info(
                "audit",
                seq=seq,
                action=event.action,
                outcome=event.outcome,
                actor_id=event.actor_id,
                actor_kind=event.actor_kind,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                channel=event.channel,
                request_id=event.request_id,
            )
        await session.flush()

    async def verify(self, session: AsyncSession, *, batch_size: int = 1000) -> VerifyResult:
        """Verify the chain in the database.

        Archived prefixes are vouched for by signed checkpoints, which must form
        their own unbroken chain; the event chain then resumes after the newest
        one. Events are never allowed to start anywhere else — removing old
        events without a valid checkpoint is reported as tampering.
        """
        prev_hash = GENESIS_HASH
        expected_seq = 1
        prev_signature = GENESIS_HASH
        for cp in await self.checkpoints(session):
            if (
                cp.prev_signature != prev_signature
                or cp.first_seq != expected_seq
                or cp.seq < cp.first_seq
                or not self.signed_by_us(self.checkpoint_payload(cp), cp.signature)
            ):
                return VerifyResult(
                    ok=False,
                    checked=0,
                    first_bad_seq=expected_seq,
                    archived_through=expected_seq - 1,
                    reason="invalid archive checkpoint",
                )
            prev_signature, prev_hash, expected_seq = cp.signature, cp.hash, cp.seq + 1
        archived = expected_seq - 1
        checked = 0
        while True:
            rows = (
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.seq >= expected_seq)
                    .order_by(AuditEvent.seq)
                    .limit(batch_size)
                )
            ).all()
            if not rows:
                return VerifyResult(ok=True, checked=checked, archived_through=archived)
            for event in rows:
                if (
                    event.seq != expected_seq
                    or event.prev_hash != prev_hash
                    or not self._matches(prev_hash, event)
                ):
                    return VerifyResult(
                        ok=False,
                        checked=checked,
                        first_bad_seq=expected_seq,
                        archived_through=archived,
                        reason="broken chain",
                    )
                prev_hash = event.hash
                expected_seq += 1
                checked += 1
            session.expunge_all()
