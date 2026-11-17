"""Outcomes: what came in, from where, about what, and whether targets were met.

Aggregates only — counts, medians and rates, never ticket content — and always
computed over the tickets the caller is allowed to see, so a team lead sees
their teams and an administrator sees everything.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import defer, raiseload

from zentral.db.models import Rating, System, Ticket, TicketStatus, utcnow
from zentral.schemas import CountOut, DayOut, InsightsOut, TargetOut
from zentral.security.rbac import Permission as P
from zentral.services import sla
from zentral.services.context import Ctx
from zentral.services.tickets import ACTIVE_STATUSES, overdue_clause, visible_clause

MAX_ROWS = 50_000
# Insights counts; it never needs content. Loading it would decrypt the
# subject, summary and requester of every ticket in the period — measured as
# the largest CPU cost under load. ``raiseload`` makes any later use fail loudly.
METADATA_ONLY = (
    defer(Ticket.subject, raiseload=True),
    defer(Ticket.ai_summary, raiseload=True),
    defer(Ticket.thread_ref, raiseload=True),
    raiseload(Ticket.requester),
    raiseload(Ticket.team),
    raiseload(Ticket.system),
)


def _target(states: list[str]) -> TargetOut:
    met = states.count("met")
    missed = states.count("late") + states.count("breached")
    measured = met + missed
    return TargetOut(met=met, missed=missed, rate=round(met / measured, 3) if measured else None)


def _top(counter: Counter[str], limit: int = 8) -> list[CountOut]:
    items = counter.most_common()
    head = [CountOut(label=label, count=count) for label, count in items[:limit]]
    rest = sum(count for _, count in items[limit:])
    if rest:
        head.append(CountOut(label="Other", count=rest))
    return head


async def insights(ctx: Ctx, days: int = 30) -> InsightsOut:
    ctx.require(P.TICKET_READ, "ticket")
    now = utcnow()
    since = now - timedelta(days=days)
    scope = visible_clause(ctx)

    created = (
        await ctx.session.execute(
            select(Ticket, System.name)
            .outerjoin(System, System.id == Ticket.system_id)
            .where(scope, Ticket.created_at >= since)
            .options(*METADATA_ONLY)
            .limit(MAX_ROWS)
        )
    ).all()
    resolved_days = (
        await ctx.session.scalars(
            select(Ticket.resolved_at).where(
                scope,
                Ticket.resolved_at >= since,
                Ticket.status.in_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
            )
        )
    ).all()

    per_day_in: Counter[date] = Counter(t.created_at.date() for t, _ in created)
    per_day_out: Counter[date] = Counter(r.date() for r in resolved_days if r)
    first = since.date()
    timeline = [
        DayOut(
            day=first + timedelta(days=i),
            created=per_day_in[first + timedelta(days=i)],
            resolved=per_day_out[first + timedelta(days=i)],
        )
        for i in range(days + 1)
    ]

    response_minutes = [
        (t.first_response_at - t.created_at).total_seconds() / 60
        for t, _ in created
        if t.first_response_at
    ]
    resolve_hours = [
        max(0.0, (t.resolved_at - t.created_at).total_seconds() - (t.paused_seconds or 0)) / 3600
        for t, _ in created
        if t.resolved_at
    ]

    backlog = (
        await ctx.session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((overdue_clause(), 1), else_=0)), 0),
            ).where(scope, Ticket.status.in_(ACTIVE_STATUSES))
        )
    ).one()
    oldest = await ctx.session.scalar(
        select(func.min(Ticket.created_at)).where(scope, Ticket.status.in_(ACTIVE_STATUSES))
    )
    waiting = await ctx.session.scalar(
        select(func.count())
        .select_from(Ticket)
        .where(
            scope,
            Ticket.status.in_(ACTIVE_STATUSES),
            Ticket.paused_at.is_not(None),
        )
    )

    scores = list(
        (
            await ctx.session.scalars(
                select(Rating.score)
                .join(Ticket, Ticket.id == Rating.ticket_id)
                .where(scope, Rating.created_at >= since)
            )
        ).all()
    )

    return InsightsOut(
        days=days,
        created=len(created),
        resolved=len(resolved_days),
        open_now=int(backlog[0]),
        overdue_now=int(backlog[1]),
        waiting_on_requester=int(waiting or 0),
        oldest_open_days=(now - oldest).days if oldest else None,
        median_first_response_minutes=round(statistics.median(response_minutes))
        if response_minutes
        else None,
        median_resolution_hours=round(statistics.median(resolve_hours), 1)
        if resolve_hours
        else None,
        first_response=_target([sla.response_view(t, now).state for t, _ in created]),
        resolution=_target([sla.resolve_view(t, ctx.settings, now).state for t, _ in created]),
        timeline=timeline,
        by_system=_top(Counter(name or "No system yet" for _, name in created)),
        by_category=_top(
            Counter(t.category or t.ai_category or "Uncategorised" for t, _ in created)
        ),
        by_channel=_top(Counter(t.channel for t, _ in created)),
        by_priority=[
            CountOut(label=p, count=n)
            for p in ("urgent", "high", "normal", "low")
            if (n := sum(1 for t, _ in created if t.priority == p))
        ],
        ratings=len(scores),
        rating_average=round(statistics.fmean(scores), 2) if scores else None,
        ratings_by_score=[
            CountOut(label=str(score), count=scores.count(score)) for score in (5, 4, 3, 2, 1)
        ]
        if scores
        else [],
    )
