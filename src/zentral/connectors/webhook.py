"""Generic webhook connector — the simplest way for a service to raise an issue.

Authentication: a Zentral-issued bearer token (``Authorization: Bearer zis_…``).
The token is shown once when the source is created; only its SHA-256 hash is
stored. It grants exactly one thing: creating messages on this one source.
"""

from __future__ import annotations

import json
import uuid
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError

from zentral.connectors.base import (
    Capability,
    Connector,
    InboundMessage,
    WebhookAuthError,
    WebhookPayloadError,
    WebhookRequest,
    WebhookResult,
)
from zentral.security.crypto import constant_time_equals, hash_token

_SEVERITY_TO_PRIORITY = {
    "critical": "urgent",
    "urgent": "urgent",
    "high": "high",
    "warning": "normal",
    "normal": "normal",
    "info": "low",
    "low": "low",
}


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=100_000)
    external_id: str | None = Field(default=None, min_length=1, max_length=200)
    thread_key: str | None = Field(default=None, min_length=1, max_length=200)
    severity: Literal["critical", "urgent", "high", "warning", "normal", "info", "low"] = "normal"
    requester_email: EmailStr | None = None
    requester_name: str | None = Field(default=None, max_length=200)


class WebhookConnector(Connector):
    name: ClassVar[str] = "webhook"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.WEBHOOK})
    uses_ingest_token: ClassVar[bool] = True

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        expected = self.ctx.token_hash
        if (
            scheme.lower() != "bearer"
            or not token
            or not expected
            or not constant_time_equals(hash_token(token.strip()), expected)
        ):
            raise WebhookAuthError
        try:
            payload = WebhookPayload.model_validate(json.loads(request.body))
        except (ValueError, ValidationError) as exc:
            raise WebhookPayloadError("invalid payload") from exc

        external_id = (
            payload.external_id or request.headers.get("idempotency-key") or str(uuid.uuid4())
        )
        message = InboundMessage(
            external_id=external_id[:200],
            subject=payload.title,
            body=payload.description or payload.title,
            sender_kind="email" if payload.requester_email else None,
            sender_id=payload.requester_email,
            sender_email=payload.requester_email,
            sender_name=payload.requester_name,
            thread_id=payload.thread_key,
            priority=_SEVERITY_TO_PRIORITY[payload.severity],
        )
        return WebhookResult(messages=[message], response={"tickets": "{ticket_keys}"})
