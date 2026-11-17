"""Generic webhook connector — the simplest way for a service to raise an issue.

Authentication: a Zentral-issued bearer token (``Authorization: Bearer zis_…``).
The token is shown once when the source is created; only its SHA-256 hash is
stored. It grants exactly one thing: creating messages on this one source.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from zentral.connectors.base import (
    Capability,
    Connector,
    InboundAttachment,
    InboundMessage,
    WebhookAuthError,
    WebhookPayloadError,
    WebhookRequest,
    WebhookResult,
)
from zentral.schemas import Email
from zentral.security.crypto import constant_time_equals, hash_token
from zentral.text import single_line

_SEVERITY_TO_PRIORITY = {
    "critical": "urgent",
    "urgent": "urgent",
    "high": "high",
    "warning": "normal",
    "normal": "normal",
    "info": "low",
    "low": "low",
}


MAX_FILES = 3
# Base64 makes files a third larger; the per-file size is checked again after decoding.
MAX_BASE64 = 36 * 1024 * 1024


class WebhookFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=MAX_BASE64)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=100_000)
    external_id: str | None = Field(default=None, min_length=1, max_length=200)
    thread_key: str | None = Field(default=None, min_length=1, max_length=200)
    system: str | None = Field(default=None, min_length=1, max_length=64)
    severity: Literal["critical", "urgent", "high", "warning", "normal", "info", "low"] = "normal"
    requester_email: Email | None = None
    requester_name: str | None = Field(default=None, max_length=200)
    # Facts about where the report came from (page, app version, browser...),
    # shown to the desk under the description.
    context: dict[
        Annotated[str, StringConstraints(min_length=1, max_length=40)],
        Annotated[str, StringConstraints(max_length=500)],
    ] = Field(default_factory=dict, max_length=20)
    attachments: list[WebhookFile] = Field(default_factory=list, max_length=MAX_FILES)


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

        try:
            files = tuple(
                InboundAttachment(f.filename, base64.b64decode(f.content_base64, validate=True))
                for f in payload.attachments
            )
        except (ValueError, binascii.Error) as exc:
            raise WebhookPayloadError("attachment is not valid base64") from exc
        body = payload.description or payload.title
        if payload.context:
            facts = "\n".join(
                f"{single_line(k, 40)}: {single_line(v, 500)}" for k, v in payload.context.items()
            )
            body = f"{body}\n\n— Context —\n{facts}"
        external_id = (
            payload.external_id or request.headers.get("idempotency-key") or str(uuid.uuid4())
        )
        message = InboundMessage(
            external_id=external_id[:200],
            subject=payload.title,
            body=body,
            sender_kind="email" if payload.requester_email else None,
            sender_id=payload.requester_email,
            sender_email=payload.requester_email,
            sender_name=payload.requester_name,
            thread_id=payload.thread_key,
            priority=_SEVERITY_TO_PRIORITY[payload.severity],
            system=payload.system,
            attachments=files,
        )
        return WebhookResult(messages=[message], response={"tickets": "{ticket_keys}"})
