"""Microsoft Teams connector (Outgoing Webhook).

A team owner adds Zentral as an *Outgoing Webhook* to a Teams team. Members
then raise an issue by @mentioning it in a channel. Teams signs each request
with HMAC-SHA256 using a shared secret that Teams generates when the webhook
is created; that secret is stored (encrypted) as the source's ``hmac_secret``.

Replies from agents are posted through an optional Teams *Workflows* webhook
URL (``reply_webhook_url``), restricted to allow-listed Microsoft hosts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Any, ClassVar

from zentral.connectors.base import (
    Capability,
    Connector,
    ConnectorError,
    InboundMessage,
    OutboundMessage,
    WebhookAuthError,
    WebhookPayloadError,
    WebhookRequest,
    WebhookResult,
)
from zentral.text import html_to_text, single_line


class TeamsConnector(Connector):
    name: ClassVar[str] = "teams"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.WEBHOOK, Capability.SEND}
    )
    secret_fields: ClassVar[frozenset[str]] = frozenset({"hmac_secret", "reply_webhook_url"})
    required_secrets: ClassVar[frozenset[str]] = frozenset({"hmac_secret"})

    def _verify(self, request: WebhookRequest) -> None:
        scheme, _, provided = request.headers.get("authorization", "").partition(" ")
        secret = self.ctx.secrets.get("hmac_secret")
        if scheme != "HMAC" or not provided or not secret:
            raise WebhookAuthError
        try:
            key = base64.b64decode(secret, validate=True)
            signature = base64.b64decode(provided.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise WebhookAuthError from exc
        expected = hmac.new(key, request.body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise WebhookAuthError

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        self._verify(request)
        try:
            activity: dict[str, Any] = json.loads(request.body)
            activity_id = str(activity["id"])
            sender = activity.get("from") or {}
            text = html_to_text(str(activity.get("text") or ""))
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookPayloadError("invalid activity") from exc
        if not text:
            return WebhookResult(
                response={"type": "message", "text": "Please describe the issue in your message."},
                status_code=200,
            )
        sender_id = str(sender.get("aadObjectId") or sender.get("id") or "") or None
        conversation = activity.get("conversation") or {}
        message = InboundMessage(
            external_id=activity_id,
            subject=single_line(text, 120),
            body=text,
            sender_kind="teams" if sender_id else None,
            sender_id=sender_id,
            sender_name=str(sender.get("name") or "") or None,
            thread_id=str(conversation.get("id") or "") or None,
        )
        return WebhookResult(
            messages=[message],
            response={"type": "message", "text": "Thanks — this is logged as {ticket_keys}."},
            status_code=200,
        )

    async def send(self, message: OutboundMessage) -> None:
        url = self.ctx.secrets.get("reply_webhook_url")
        if not url:
            raise ConnectorError("no reply_webhook_url configured for this Teams source")
        payload = {"type": "message", "text": f"**{message.ticket_key}** — {message.body}"}
        resp = await self.ctx.http.post(
            self.check_outbound_url(url), json=payload, timeout=15, follow_redirects=False
        )
        if resp.status_code >= 300:
            raise ConnectorError(f"Teams webhook returned {resp.status_code}")
