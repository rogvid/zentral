"""Ticket classification: redact → ask the provider → validate → store → automate."""

from __future__ import annotations

import asyncio
from importlib.metadata import entry_points

import httpx
import structlog
from sqlalchemy import select

from zentral.ai.base import AIError, AIProvider, Classification, ClassificationInput
from zentral.ai.providers.rules import RulesProvider
from zentral.config import Settings
from zentral.db.models import Message, Ticket, TicketPriority
from zentral.security.redaction import redact
from zentral.services.automation import apply_rules
from zentral.services.context import Ctx
from zentral.text import single_line

log = structlog.get_logger(__name__)

GROUP = "zentral.ai_providers"
MAX_INPUT_CHARS = 8000


def load_provider(settings: Settings, http: httpx.AsyncClient) -> AIProvider:
    for ep in entry_points(group=GROUP):
        if ep.name == settings.ai_provider:
            klass = ep.load()
            if not (isinstance(klass, type) and issubclass(klass, AIProvider)):
                raise TypeError(f"entry point {ep.name!r} is not an AIProvider")
            provider: AIProvider = klass(settings, http)
            return provider
    raise ValueError(f"unknown AI provider {settings.ai_provider!r}")


def _normalise(result: Classification, categories: tuple[str, ...]) -> Classification:
    """Never trust model output: force it back into the allowed value space."""
    category = result.category.strip().lower()
    if category not in categories:
        category = "other" if "other" in categories else categories[0]
        confidence = 0.0
    else:
        confidence = min(1.0, max(0.0, float(result.confidence)))
    return Classification(
        category=category,
        priority=result.priority,
        summary=single_line(result.summary, 300),
        confidence=round(confidence, 3),
    )


async def classify_ticket(ctx: Ctx, provider: AIProvider, ticket_id: int) -> None:
    ticket = await ctx.session.get(Ticket, ticket_id)
    if ticket is None or ticket.scrubbed_at is not None:
        return
    first = await ctx.session.scalar(
        select(Message).where(Message.ticket_id == ticket_id).order_by(Message.created_at).limit(1)
    )
    subject, body = ticket.subject, (first.body if first else "")[:MAX_INPUT_CHARS]
    redacted = ctx.settings.ai_redact_pii
    if redacted:
        subject, body = redact(subject), redact(body)
    categories = tuple(ctx.settings.categories)
    inp = ClassificationInput(subject=subject, body=body, categories=categories)

    used = provider
    try:
        async with asyncio.timeout(ctx.settings.ai_timeout_seconds + 5):
            raw = await provider.classify(inp)
    except (AIError, TimeoutError) as exc:
        # Intake must never depend on model availability.
        log.warning("ai_provider_failed", provider=provider.name, reason=str(exc))
        used = RulesProvider(ctx.settings, provider.http)
        raw = await used.classify(inp)
    result = _normalise(raw, categories)

    ticket.ai_category = result.category
    ticket.ai_priority = result.priority
    ticket.ai_confidence = result.confidence
    ticket.ai_summary = result.summary or None
    ticket.ai_model = used.model_label[:128]

    applied = result.confidence >= ctx.settings.ai_min_confidence
    if applied:
        # Suggestions only fill gaps; they never overwrite a human decision.
        if ticket.category is None:
            ticket.category = result.category
        if ticket.priority == TicketPriority.NORMAL:
            ticket.priority = result.priority
    ctx.audit(
        "ticket.classified",
        "ticket",
        ticket.id,
        provider=used.name,
        model=ticket.ai_model,
        on_prem=used.on_prem,
        input_redacted=redacted,
        category=result.category,
        priority=result.priority,
        confidence=result.confidence,
        applied=applied,
        fallback=used is not provider,
    )
    await apply_rules(ctx, ticket)
