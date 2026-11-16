"""Connector plug-in contract.

A connector adapts one external channel (a mailbox, a chat platform, a
monitoring system) to Zentral's normalised message model. It can implement any
combination of three capabilities:

* ``WEBHOOK`` — the channel pushes HTTP requests to ``/api/v1/hooks/{source_id}``.
  The connector **must authenticate every request itself** (signature, token)
  and raise ``WebhookAuthError`` otherwise. The endpoint has no other auth.
* ``POLL`` — the worker periodically asks the connector for new messages.
* ``SEND`` — the connector can deliver an agent's reply to the requester.

Connectors never touch the database. They return ``InboundMessage`` objects and
the core takes care of de-duplication, threading, contacts, encryption,
classification and auditing. That keeps plug-ins small and keeps the
security-critical logic in one reviewed place.

See docs/extending/connectors.md for a walkthrough.
"""

from __future__ import annotations

import uuid
from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

from zentral.config import Settings


class Capability(StrEnum):
    WEBHOOK = "webhook"
    POLL = "poll"
    SEND = "send"


class ConnectorError(Exception):
    """Operational failure talking to the external system (retried by the worker)."""


class WebhookAuthError(Exception):
    """The webhook request could not be authenticated. Always answered with a bare 401."""


class WebhookPayloadError(Exception):
    """Authenticated but malformed request (400)."""


class NoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A message from the outside world, normalised."""

    external_id: str  # unique per source; used for idempotent de-duplication
    subject: str
    body: str  # plain text only
    sender_kind: str | None = None  # identity namespace: "email", "teams", "slack", ...
    sender_id: str | None = None  # stable identifier within that namespace
    sender_email: str | None = None
    sender_name: str | None = None
    thread_id: str | None = None  # external conversation id, for threading
    priority: str | None = None
    system: str | None = None  # key of the system the message concerns, if the sender knows
    received_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    ticket_key: str
    subject: str
    body: str
    recipient_email: str | None
    recipient_name: str | None
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    headers: Mapping[str, str]  # lower-cased names
    body: bytes
    query: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookResult:
    messages: list[InboundMessage] = field(default_factory=list)
    # Optional reply body. ``{ticket_keys}`` in string values is substituted by the core.
    response: dict[str, Any] | None = None
    status_code: int = 202


@dataclass(slots=True)
class PollResult:
    messages: list[InboundMessage]
    cursor: dict[str, Any]
    # Opaque references handed back to ``Connector.acknowledge`` once the
    # messages are safely stored (e.g. to mark mail as read).
    ack: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One line of a channel self-test, shown to administrators. Never put
    secrets or message content in ``detail``."""

    name: str
    status: str  # ok | warn | fail
    detail: str


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    source_id: uuid.UUID
    config: dict[str, Any]
    secrets: dict[str, Any]
    token_hash: str | None
    http: httpx.AsyncClient
    settings: Settings


class Connector(ABC):
    """Base class for all connectors."""

    #: registry name; must match the entry-point name
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]
    #: validated, non-secret configuration (stored in clear, shown to admins)
    config_model: ClassVar[type[BaseModel]] = NoConfig
    #: names of secret values (encrypted at rest, write-only through the API)
    secret_fields: ClassVar[frozenset[str]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    #: whether the source needs a Zentral-issued ingest token
    uses_ingest_token: ClassVar[bool] = False

    def __init__(self, ctx: ConnectorContext) -> None:
        self.ctx = ctx
        self.config = self.config_model.model_validate(ctx.config)

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        raise NotImplementedError

    async def poll(self, cursor: dict[str, Any]) -> PollResult:
        raise NotImplementedError

    async def send(self, message: OutboundMessage) -> None:
        raise NotImplementedError

    async def acknowledge(self, refs: list[str]) -> None:  # noqa: B027 - optional hook
        """Called after polled messages were committed. Optional; failures are
        logged and never undo the ingestion."""

    async def diagnose(self) -> list[Diagnostic]:
        """Self-test run when an administrator presses *Test*. Optional."""
        return []

    # -- helpers for implementers -------------------------------------------------

    def check_outbound_url(self, url: str) -> str:
        """Guard against SSRF: only https to an allow-listed host."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        allowed = any(
            host == suffix or host.endswith("." + suffix)
            for suffix in self.ctx.settings.outbound_host_allowlist
        )
        if parts.scheme != "https" or not allowed or parts.username or parts.password:
            raise ConnectorError("outbound URL is not allow-listed")
        return url
