"""Archiving the audit trail out of the database.

The trail only grows. To keep the database lean without losing evidence, old
events are moved to signed archive files, in three deliberate steps:

1. **archive** — verify the whole chain, then write every not-yet-archived
   event older than a cut-off to ``audit-<first>-<last>.jsonl`` with a
   ``.manifest.json`` beside it. The manifest records the range, the hashes at
   both ends and the file's SHA-256, and is signed with the audit key. The
   files are re-read and verified before the command reports success.
2. *(you)* copy both files to write-once storage.
3. **prune** — verify the archive again, check that it matches the database
   event for event at its end, then, in one transaction: write a signed
   checkpoint, delete exactly the archived range, and record ``audit.pruned``
   in the live chain. The database guard permits that one delete and nothing
   else.

From then on ``verify`` checks the checkpoints and resumes the chain after
the newest one, and ``verify_archive`` checks any archive file on its own.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zentral.audit.trail import GENESIS_HASH, AuditTrail, PendingEvent, RequestMeta
from zentral.db.models import AuditCheckpoint, AuditEvent, utcnow
from zentral.security.principal import Principal

FORMAT = "zentral-audit-archive/1"
FIELDS = (
    "seq",
    "id",
    "occurred_at",
    "actor_id",
    "actor_kind",
    "actor_roles",
    "action",
    "resource_type",
    "resource_id",
    "outcome",
    "channel",
    "request_id",
    "ip",
    "details",
    "prev_hash",
    "hash",
)


class ArchiveError(RuntimeError):
    """The archive or the chain is not in a state that allows this step."""


@dataclass(frozen=True, slots=True)
class Manifest:
    path: Path
    first_seq: int
    last_seq: int
    first_prev_hash: str
    last_hash: str
    events: int
    sha256: str
    before: str
    last_occurred_at: str

    @property
    def data_path(self) -> Path:
        return self.path.with_name(self.path.name.replace(".manifest.json", ".jsonl"))


def _row(event: AuditEvent) -> dict[str, Any]:
    return {
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
        "prev_hash": event.prev_hash,
        "hash": event.hash,
    }


def _event(row: dict[str, Any]) -> AuditEvent:
    if set(row) != set(FIELDS):
        raise ArchiveError(f"unexpected fields in archived event {row.get('seq')}")
    return AuditEvent(
        seq=int(row["seq"]),
        id=uuid.UUID(row["id"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        actor_id=row["actor_id"],
        actor_kind=row["actor_kind"],
        actor_roles=list(row["actor_roles"]),
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        outcome=row["outcome"],
        channel=row["channel"],
        request_id=row["request_id"],
        ip=row["ip"],
        details=row["details"],
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


async def _archived_through(session: AsyncSession) -> tuple[int, str]:
    """(seq, hash) of the newest checkpoint, or the genesis."""
    cp = await session.scalar(select(AuditCheckpoint).order_by(AuditCheckpoint.seq.desc()).limit(1))
    return (cp.seq, cp.hash) if cp else (0, GENESIS_HASH)


# --- 1. archive ----------------------------------------------------------------------------


async def write_archive(
    session: AsyncSession,
    trail: AuditTrail,
    before: datetime,
    directory: Path,
    *,
    start_after: int | None = None,
) -> Manifest | None:
    """Write events older than ``before`` that are not archived yet. ``None`` if there are none.

    ``start_after`` continues after an earlier archive that has not been pruned
    yet (so archives can be written daily and pruned later).
    """
    result = await trail.verify(session)
    if not result.ok:
        raise ArchiveError(f"the audit chain does not verify (seq {result.first_bad_seq})")
    checkpoint_seq, _ = await _archived_through(session)
    after = max(checkpoint_seq, start_after or 0)
    last = await session.scalar(
        select(func.max(AuditEvent.seq)).where(
            AuditEvent.occurred_at < before, AuditEvent.seq > after
        )
    )
    if not last:
        return None
    first = after + 1
    # Batch housekeeping (CLI or worker), not request handling: plain file I/O is fine.
    directory.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    stem = f"audit-{first:010d}-{last:010d}"
    data_path = directory / f"{stem}.jsonl"
    manifest_path = directory / f"{stem}.manifest.json"
    if data_path.exists() or manifest_path.exists():
        raise ArchiveError(f"{data_path.name} already exists; refusing to overwrite an archive")

    digest = hashlib.sha256()
    first_prev = last_hash = last_at = ""
    count = 0
    with data_path.open("x", encoding="utf-8") as fh:
        stream = await session.stream_scalars(
            select(AuditEvent)
            .where(AuditEvent.seq >= first, AuditEvent.seq <= last)
            .order_by(AuditEvent.seq)
        )
        async for event in stream:
            line = json.dumps(_row(event), sort_keys=True, separators=(",", ":")) + "\n"
            fh.write(line)
            digest.update(line.encode())
            first_prev = first_prev or event.prev_hash
            last_hash = event.hash
            last_at = event.occurred_at.isoformat()
            count += 1
    payload = {
        "format": FORMAT,
        "first_seq": first,
        "last_seq": last,
        "first_prev_hash": first_prev,
        "last_hash": last_hash,
        "events": count,
        "sha256": digest.hexdigest(),
        "before": before.isoformat(),
        "last_occurred_at": last_at,
        "created_at": utcnow().isoformat(),
    }
    payload["signature"] = trail.sign(payload)
    with manifest_path.open("x", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return verify_archive(manifest_path, trail)


# --- verification of a file on its own -------------------------------------------------------


def verify_archive(manifest_path: Path, trail: AuditTrail) -> Manifest:
    """Check an archive: manifest signature, file hash, and the chain inside it."""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"cannot read manifest {manifest_path.name}") from exc
    signature = str(payload.pop("signature", ""))
    if payload.get("format") != FORMAT or not trail.signed_by_us(payload, signature):
        raise ArchiveError("the manifest is not a valid, signed Zentral audit archive")
    manifest = Manifest(
        path=manifest_path,
        first_seq=int(payload["first_seq"]),
        last_seq=int(payload["last_seq"]),
        first_prev_hash=str(payload["first_prev_hash"]),
        last_hash=str(payload["last_hash"]),
        events=int(payload["events"]),
        sha256=str(payload["sha256"]),
        before=str(payload["before"]),
        last_occurred_at=str(payload["last_occurred_at"]),
    )
    digest = hashlib.sha256()
    prev_hash, expected = manifest.first_prev_hash, manifest.first_seq
    try:
        with manifest.data_path.open("rb") as fh:
            for raw in fh:
                digest.update(raw)
                event = _event(json.loads(raw))
                if (
                    event.seq != expected
                    or event.prev_hash != prev_hash
                    or not trail.matches(prev_hash, event)
                ):
                    raise ArchiveError(f"the archived chain is broken at seq {expected}")
                prev_hash, expected = event.hash, expected + 1
    except OSError as exc:
        raise ArchiveError(f"cannot read {manifest.data_path.name}") from exc
    if digest.hexdigest() != manifest.sha256:
        raise ArchiveError("the archive file does not match its manifest")
    if expected - 1 != manifest.last_seq or prev_hash != manifest.last_hash:
        raise ArchiveError("the archive is incomplete")
    if expected - manifest.first_seq != manifest.events:
        raise ArchiveError("the archive's event count does not match its manifest")
    return manifest


# --- 3. prune ------------------------------------------------------------------------------


async def prune(
    session: AsyncSession, trail: AuditTrail, manifest_path: Path, actor: Principal
) -> int:
    """Remove the events an archive holds from the database. Returns how many."""
    manifest = verify_archive(manifest_path, trail)
    async with trail.serialized(session):
        checkpoint_seq, checkpoint_hash = await _archived_through(session)
        if manifest.first_seq != checkpoint_seq + 1 or manifest.first_prev_hash != checkpoint_hash:
            raise ArchiveError(
                f"this archive starts at seq {manifest.first_seq}; the next one to prune "
                f"must start at seq {checkpoint_seq + 1}"
            )
        stored = await session.scalar(
            select(AuditEvent.hash).where(AuditEvent.seq == manifest.last_seq)
        )
        if stored != manifest.last_hash:
            raise ArchiveError("the archive does not match the events in the database")
        result = await trail.verify(session)
        if not result.ok:
            raise ArchiveError(f"the audit chain does not verify (seq {result.first_bad_seq})")

        latest = await session.scalar(
            select(AuditCheckpoint).order_by(AuditCheckpoint.seq.desc()).limit(1)
        )
        checkpoint = AuditCheckpoint(
            seq=manifest.last_seq,
            hash=manifest.last_hash,
            first_seq=manifest.first_seq,
            events=manifest.events,
            archive_sha256=manifest.sha256,
            created_at=utcnow(),
            created_by=actor.subject,
            prev_signature=latest.signature if latest else GENESIS_HASH,
            signature="",
        )
        checkpoint.signature = trail.sign(trail.checkpoint_payload(checkpoint))
        session.add(checkpoint)
        await session.flush()
        if session.get_bind().dialect.name == "postgresql":
            # Tell the database guard which delete this transaction may make.
            await session.execute(
                text("SELECT set_config('zentral.audit_prune', :seq, true)"),
                {"seq": str(manifest.last_seq)},
            )
        deleted = await session.execute(
            delete(AuditEvent).where(AuditEvent.seq <= manifest.last_seq)
        )
        count = int(getattr(deleted, "rowcount", 0) or 0)
        await trail.write(
            session,
            [
                PendingEvent(
                    "audit.pruned",
                    "audit",
                    str(manifest.last_seq),
                    details={
                        "first_seq": manifest.first_seq,
                        "last_seq": manifest.last_seq,
                        "events": count,
                        "archive_sha256": manifest.sha256,
                    },
                )
            ],
            actor,
            RequestMeta(channel="cli"),
        )
        await session.commit()
    return count
