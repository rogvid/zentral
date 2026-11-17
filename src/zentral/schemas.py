"""API data transfer objects.

Input models forbid unknown fields and bound every string, so oversized or
unexpected input is rejected at the edge.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from zentral.db.models import TicketPriority, TicketStatus

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# Deliberately permissive: service desks receive mail from internal domains
# (.local, .corp, .internal) that strict validators reject. The address is only
# ever used as an identifier and as a Graph API recipient, never in a shell or header.
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, max_length=320, pattern=r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,255}$"
    ),
]
Body = Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[\w.-]+$")
]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- tickets -----------------------------------------------------------------


class RequesterOut(Out):
    id: uuid.UUID
    display_name: str
    email: str | None


class TicketOut(Out):
    id: int
    key: str
    subject: str
    status: TicketStatus
    priority: TicketPriority
    category: str | None
    tags: list[str]
    channel: str
    team_id: uuid.UUID | None
    team_name: str | None
    assignee_id: str | None
    assignee_name: str | None
    system_id: uuid.UUID | None
    system_key: str | None
    system_name: str | None
    requester: RequesterOut | None
    ai_category: str | None
    ai_priority: str | None
    ai_system_key: str | None
    ai_confidence: float | None
    ai_summary: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    # Service-level targets. States: met, late, due, breached, paused, none.
    response_due_at: datetime | None = None
    response_state: str = "none"
    first_response_at: datetime | None = None
    resolve_due_at: datetime | None = None
    resolve_state: str = "none"
    # "business" (working hours) or "24x7": which clock the targets run on.
    target_clock: str = "24x7"
    # Set when this ticket was folded into another one.
    merged_into_id: int | None = None
    merged_into_key: str | None = None
    # The requester objected to automated analysis: no AI triage, hidden from AI agents.
    ai_excluded: bool = False
    pii_redacted: bool


class TicketPage(Out):
    items: list[TicketOut]
    total: int
    limit: int
    offset: int


class MessageOut(Out):
    id: uuid.UUID
    kind: str
    body: str
    author_name: str
    author_type: Literal["requester", "staff", "system"]
    delivery_status: str
    created_at: datetime


class ActivityOut(Out):
    """One entry of a ticket's history. Derived from the audit trail; no content."""

    occurred_at: datetime
    action: str
    actor_name: str
    actor_kind: str
    channel: str
    details: dict[str, Any]


class CountOut(Out):
    label: str
    count: int


class DayOut(Out):
    day: date
    created: int
    resolved: int


class TargetOut(Out):
    met: int
    missed: int
    rate: float | None  # met / (met + missed); None when nothing was measured


class InsightsOut(Out):
    days: int
    created: int
    resolved: int
    open_now: int
    overdue_now: int
    waiting_on_requester: int
    oldest_open_days: int | None
    median_first_response_minutes: int | None
    median_resolution_hours: float | None
    first_response: TargetOut
    resolution: TargetOut
    timeline: list[DayOut]
    by_system: list[CountOut]
    by_category: list[CountOut]
    by_channel: list[CountOut]
    by_priority: list[CountOut]


class TicketCreate(In):
    subject: Short
    body: Body
    requester_email: Email | None = None
    requester_name: Annotated[str, StringConstraints(max_length=200)] | None = None
    priority: TicketPriority = TicketPriority.NORMAL
    category: str | None = Field(default=None, max_length=64)
    team_id: uuid.UUID | None = None
    system_id: uuid.UUID | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=20)


class TicketUpdate(In):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category: str | None = Field(default=None, max_length=64)
    tags: list[Tag] | None = Field(default=None, max_length=20)
    team_id: uuid.UUID | None = None
    system_id: uuid.UUID | None = None
    assignee_id: str | None = Field(default=None, max_length=64)


class MessageCreate(In):
    kind: Literal["outbound", "note"]
    body: Body


class MergeIn(In):
    into_ticket_id: int = Field(ge=1)


# --- notifications ----------------------------------------------------------------


class NotificationOut(Out):
    id: uuid.UUID
    kind: str
    ticket_id: int
    ticket_key: str
    created_at: datetime
    read_at: datetime | None


class NotificationPage(Out):
    items: list[NotificationOut]
    unread: int


class NotificationsRead(In):
    """Which of one's notifications to mark read; neither field means all of them."""

    ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    ticket_id: int | None = Field(default=None, ge=1)


# --- saved replies ----------------------------------------------------------------


class TemplateIn(In):
    name: Short
    body: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    team_id: uuid.UUID | None = None


class TemplateUpdate(In):
    name: Short | None = None
    body: Annotated[str, StringConstraints(min_length=1, max_length=20_000)] | None = None
    team_id: uuid.UUID | None = None


class TemplateOut(Out):
    id: uuid.UUID
    name: str
    body: str
    team_id: uuid.UUID | None
    team_name: str | None
    updated_at: datetime


# --- administration ------------------------------------------------------------


class TeamIn(In):
    name: Short
    entra_group_id: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-fA-F-]{36}$")


class TeamOut(Out):
    id: uuid.UUID
    name: str
    entra_group_id: str | None
    member_ids: list[str] = Field(default_factory=list)


SystemKey = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
]


# Per-priority target overrides: {"urgent": 0.5, "high": 2}. At most a year.
TargetHours = dict[TicketPriority, Annotated[float, Field(gt=0, le=8760)]]


class SystemIn(In):
    key: SystemKey
    name: Short
    description: Annotated[str, StringConstraints(max_length=500)] = ""
    keywords: list[Short] = Field(default_factory=list, max_length=30)
    default_team_id: uuid.UUID | None = None
    active: bool = True
    sla_response_hours: TargetHours = Field(default_factory=dict)
    sla_resolve_hours: TargetHours = Field(default_factory=dict)


class SystemOut(Out):
    id: uuid.UUID
    key: str
    name: str
    description: str
    keywords: list[str]
    default_team_id: uuid.UUID | None
    active: bool
    sla_response_hours: dict[str, float] = Field(default_factory=dict)
    sla_resolve_hours: dict[str, float] = Field(default_factory=dict)


class TeamMemberIn(In):
    user_id: str = Field(min_length=1, max_length=64)


class UserOut(Out):
    id: str
    kind: str
    display_name: str
    email: str


class SourceIn(In):
    name: Short
    connector: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    default_team_id: uuid.UUID | None = None
    default_system_id: uuid.UUID | None = None
    enabled: bool = True


class SourceUpdate(In):
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None
    default_team_id: uuid.UUID | None = None
    default_system_id: uuid.UUID | None = None
    enabled: bool | None = None


class SourceOut(Out):
    id: uuid.UUID
    name: str
    connector: str
    config: dict[str, Any]
    default_team_id: uuid.UUID | None
    default_system_id: uuid.UUID | None
    enabled: bool
    has_secrets: bool
    has_token: bool
    hook_url: str | None
    capabilities: list[str] = Field(default_factory=list)
    last_ok_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    created_at: datetime


class SourceCreated(SourceOut):
    # Shown exactly once; only a hash is stored.
    ingest_token: str | None


class DiagnosticOut(Out):
    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str


class RuleConditions(In):
    channel: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    priority: TicketPriority | None = None
    source_id: uuid.UUID | None = None
    system_id: uuid.UUID | None = None
    subject_contains: list[Short] = Field(default_factory=list, max_length=20)


class RuleActions(In):
    set_team_id: uuid.UUID | None = None
    set_system_id: uuid.UUID | None = None
    set_priority: TicketPriority | None = None
    set_assignee_id: str | None = Field(default=None, max_length=64)
    add_tags: list[Tag] = Field(default_factory=list, max_length=10)
    stop: bool = False


class RuleIn(In):
    name: Short
    position: int = Field(default=100, ge=0, le=100_000)
    enabled: bool = True
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    actions: RuleActions = Field(default_factory=RuleActions)


class RuleOut(Out):
    id: uuid.UUID
    name: str
    position: int
    enabled: bool
    conditions: dict[str, Any]
    actions: dict[str, Any]


# --- audit & privacy -----------------------------------------------------------


class AuditEventOut(Out):
    seq: int
    id: uuid.UUID
    occurred_at: datetime
    actor_id: str
    actor_kind: str
    actor_roles: list[str]
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    channel: str
    request_id: str
    ip: str
    details: dict[str, Any]
    hash: str


class AuditPage(Out):
    items: list[AuditEventOut]
    next_before_seq: int | None


class AuditVerifyOut(Out):
    ok: bool
    checked: int
    first_bad_seq: int | None


class ContactLookup(In):
    email: Email


class ContactOut(Out):
    id: uuid.UUID
    email: str | None
    display_name: str | None
    created_at: datetime
    erased_at: datetime | None
    ai_objected_at: datetime | None
    ticket_count: int


class ObjectionIn(In):
    objected: bool


class ErasureOut(Out):
    contact_id: uuid.UUID
    tickets_scrubbed: int
    messages_scrubbed: int


class MeOut(Out):
    subject: str
    kind: str
    display_name: str
    email: str
    roles: list[str]
    permissions: list[str]
    team_ids: list[uuid.UUID]
