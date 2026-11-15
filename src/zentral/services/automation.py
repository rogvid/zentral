"""Deterministic automation rules.

Rules are evaluated in ``position`` order after a new ticket is classified.
All conditions of a rule must match (logical AND); an empty condition set
matches every ticket. Rules can only route and label — they cannot send
messages or delete anything.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from zentral.db.models import AutomationRule, Team, Ticket, User
from zentral.schemas import RuleActions, RuleConditions
from zentral.services.context import Ctx


def matches(conditions: RuleConditions, ticket: Ticket) -> bool:
    if conditions.channel and conditions.channel != ticket.channel:
        return False
    if conditions.category and conditions.category != ticket.category:
        return False
    if conditions.priority and conditions.priority != ticket.priority:
        return False
    if conditions.source_id and conditions.source_id != ticket.source_id:
        return False
    if conditions.subject_contains:
        subject = ticket.subject.casefold()
        if not any(needle.casefold() in subject for needle in conditions.subject_contains):
            return False
    return True


async def apply_rules(ctx: Ctx, ticket: Ticket) -> list[str]:
    rules = (
        await ctx.session.scalars(
            select(AutomationRule)
            .where(AutomationRule.enabled.is_(True))
            .order_by(AutomationRule.position, AutomationRule.created_at)
        )
    ).all()
    applied: list[str] = []
    for rule in rules:
        conditions = RuleConditions.model_validate(rule.conditions)
        if not matches(conditions, ticket):
            continue
        actions = RuleActions.model_validate(rule.actions)
        changes: dict[str, Any] = {}
        if (
            actions.set_team_id is not None
            and await ctx.session.get(Team, actions.set_team_id) is not None
        ):
            ticket.team_id = actions.set_team_id
            changes["team_id"] = str(actions.set_team_id)
        if actions.set_priority:
            ticket.priority = actions.set_priority
            changes["priority"] = actions.set_priority.value
        if (
            actions.set_assignee_id is not None
            and await ctx.session.get(User, actions.set_assignee_id) is not None
        ):
            ticket.assignee_id = actions.set_assignee_id
            changes["assignee_id"] = actions.set_assignee_id
        if actions.add_tags:
            ticket.tags = sorted(set(ticket.tags or []) | set(actions.add_tags))
            changes["tags_added"] = sorted(actions.add_tags)
        applied.append(str(rule.id))
        ctx.audit(
            "automation.rule_applied", "ticket", ticket.id, rule_id=str(rule.id), changes=changes
        )
        if actions.stop:
            break
    return applied
