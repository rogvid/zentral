"""Background worker.

Jobs live in the database (transactional outbox): they are enqueued in the same
transaction as the change that needs them, so nothing is lost if the process
dies between "saved the ticket" and "queued the classification".

Delivery is at-least-once; every handler is idempotent.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

import structlog
from sqlalchemy import or_, select

from zentral.ai.service import classify_ticket
from zentral.connectors.base import (
    Capability,
    Connector,
    ConnectorContext,
    ConnectorError,
    OutboundMessage,
)
from zentral.db.models import (
    DeliveryStatus,
    IngestSource,
    Job,
    JobStatus,
    Message,
    Ticket,
    utcnow,
)
from zentral.runtime import Runtime
from zentral.services import privacy
from zentral.services.context import Ctx
from zentral.services.ingest import ingest
from zentral.services.tickets import ticket_key

log = structlog.get_logger(__name__)

MAX_ATTEMPTS = 5
VISIBILITY_TIMEOUT = timedelta(minutes=15)


def instantiate(runtime: Runtime, source: IngestSource) -> Connector | None:
    klass = runtime.registry.get(source.connector)
    if klass is None:
        return None
    return klass(
        ConnectorContext(
            source_id=source.id,
            config=dict(source.config or {}),
            secrets=dict(source.secrets or {}),
            token_hash=source.token_hash,
            http=runtime.http,
            settings=runtime.settings,
        )
    )


class Worker:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self._handlers: dict[str, Callable[[Ctx, dict[str, object]], Awaitable[None]]] = {
            "classify_ticket": self._classify,
            "send_message": self._send,
            "poll_source": self._poll,
            "retention_sweep": self._retention,
        }

    # -- queue mechanics -------------------------------------------------------------

    async def _claim(self) -> Job | None:
        now = utcnow()
        async with self.rt.sessions() as session:
            job = await session.scalar(
                select(Job)
                .where(
                    or_(Job.status == JobStatus.PENDING, Job.status == JobStatus.RUNNING),
                    Job.run_after <= now,
                )
                .order_by(Job.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            # If this worker dies, the job becomes claimable again after the timeout.
            job.run_after = now + VISIBILITY_TIMEOUT
            await session.commit()
            return job

    async def run_once(self, limit: int = 50) -> int:
        """Process up to ``limit`` due jobs. Returns the number processed."""
        processed = 0
        while processed < limit and (job := await self._claim()) is not None:
            processed += 1
            await self._run(job)
        return processed

    async def _run(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        error: str | None = None
        try:
            if handler is None:
                raise ConnectorError(f"unknown job kind {job.kind}")
            async with self.rt.sessions() as session:
                ctx = self.rt.system_ctx(session, "worker")
                await handler(ctx, job.payload)
                await ctx.commit()
        except Exception as exc:
            # Only our own ConnectorError messages are known to be free of content.
            detail = str(exc) if isinstance(exc, ConnectorError) else ""
            error = f"{type(exc).__name__}: {detail}"[:500]
            log.warning("job_failed", kind=job.kind, job_id=str(job.id), attempt=job.attempts, error=error)

        async with self.rt.sessions() as session:
            ctx = self.rt.system_ctx(session, "worker")
            stored = await session.get(Job, job.id)
            if stored is None:
                return
            if error is None:
                stored.status = JobStatus.DONE
                stored.last_error = None
            elif stored.attempts >= MAX_ATTEMPTS:
                stored.status = JobStatus.FAILED
                stored.last_error = error
                await self._on_exhausted(ctx, stored)
            else:
                stored.status = JobStatus.PENDING
                stored.last_error = error
                stored.run_after = utcnow() + timedelta(seconds=15 * 2**stored.attempts)
            await ctx.commit()

    async def _on_exhausted(self, ctx: Ctx, job: Job) -> None:
        if job.kind == "send_message":
            message = await ctx.session.get(Message, uuid.UUID(str(job.payload["message_id"])))
            if message is not None:
                message.delivery_status = DeliveryStatus.FAILED
                ctx.audit(
                    "message.delivery_failed", "ticket", message.ticket_id,
                    outcome="failure", message_id=str(message.id),
                )

    # -- scheduling ---------------------------------------------------------------------

    async def schedule(self) -> None:
        """Enqueue periodic work, at most one pending job per kind/source."""
        async with self.rt.sessions() as session:
            open_jobs = (
                await session.scalars(
                    select(Job).where(
                        Job.kind.in_(["poll_source", "retention_sweep"]),
                        Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
                    )
                )
            ).all()
            polling = {str(j.payload.get("source_id")) for j in open_jobs if j.kind == "poll_source"}
            sources = (
                await session.scalars(select(IngestSource).where(IngestSource.enabled.is_(True)))
            ).all()
            for source in sources:
                klass = self.rt.registry.get(source.connector)
                if klass and Capability.POLL in klass.capabilities and str(source.id) not in polling:
                    session.add(Job(kind="poll_source", payload={"source_id": str(source.id)}))
            if not any(j.kind == "retention_sweep" for j in open_jobs):
                session.add(
                    Job(kind="retention_sweep", payload={}, run_after=utcnow() + timedelta(hours=6))
                )
            await session.commit()

    async def run_forever(self, stop: asyncio.Event) -> None:
        interval = self.rt.settings.poll_interval_seconds
        last_schedule = float("-inf")
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            if loop.time() - last_schedule >= interval:
                await self.schedule()
                last_schedule = loop.time()
            if await self.run_once() == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    pass

    # -- handlers -----------------------------------------------------------------------

    async def _classify(self, ctx: Ctx, payload: dict[str, object]) -> None:
        await classify_ticket(ctx, self.rt.ai, int(str(payload["ticket_id"])))

    async def _retention(self, ctx: Ctx, payload: dict[str, object]) -> None:
        while await privacy.retention_sweep(ctx):
            await ctx.commit()

    async def _poll(self, ctx: Ctx, payload: dict[str, object]) -> None:
        source = await ctx.session.get(IngestSource, uuid.UUID(str(payload["source_id"])))
        if source is None or not source.enabled:
            return
        connector = instantiate(self.rt, source)
        if connector is None or Capability.POLL not in connector.capabilities:
            return
        result = await connector.poll(dict(source.cursor or {}))
        for message in result.messages:
            await ingest(ctx, source, message)
        source.cursor = result.cursor

    async def _send(self, ctx: Ctx, payload: dict[str, object]) -> None:
        message = await ctx.session.get(Message, uuid.UUID(str(payload["message_id"])))
        if message is None or message.delivery_status != DeliveryStatus.PENDING:
            return  # already delivered or withdrawn: idempotent
        ticket = await ctx.session.get(Ticket, message.ticket_id)
        source = await ctx.session.get(IngestSource, ticket.source_id) if ticket else None
        connector = instantiate(self.rt, source) if source and source.enabled else None
        if ticket is None or connector is None or Capability.SEND not in connector.capabilities:
            message.delivery_status = DeliveryStatus.NOT_APPLICABLE
            return
        requester = ticket.requester
        await connector.send(
            OutboundMessage(
                ticket_key=ticket_key(ctx, ticket.id),
                subject=ticket.subject,
                body=message.body,
                recipient_email=requester.email if requester else None,
                recipient_name=requester.display_name if requester else None,
            )
        )
        message.delivery_status = DeliveryStatus.SENT
        ctx.audit(
            "message.delivered", "ticket", ticket.id,
            message_id=str(message.id), connector=source.connector if source else "",
        )
