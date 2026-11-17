"""Example connector: Slack Events API → Zentral tickets.

A complete, working plug-in in ~100 lines. It shows everything a connector
must do:

1. **authenticate** every webhook itself (Slack signs requests with
   HMAC-SHA256 over ``v0:<timestamp>:<body>`` and we reject stale timestamps
   to prevent replay),
2. **normalise** the platform's payload into ``InboundMessage``,
3. optionally **send** replies back.

Everything else — de-duplication, threading, contacts, encryption, AI
classification, auditing — is done by Zentral's core.

Slack setup: create an app with the Events API pointed at the source's webhook
URL, subscribe to ``app_mention`` (or ``message.channels``), and store the
app's *signing secret* and a bot token with ``chat:write`` as source secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
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
from zentral.text import single_line

MAX_SKEW_SECONDS = 300


class SlackConnector(Connector):
    name: ClassVar[str] = "slack"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.WEBHOOK, Capability.SEND})
    secret_fields: ClassVar[frozenset[str]] = frozenset({"signing_secret", "bot_token"})
    required_secrets: ClassVar[frozenset[str]] = frozenset({"signing_secret"})

    def _verify(self, request: WebhookRequest) -> None:
        timestamp = request.headers.get("x-slack-request-timestamp", "")
        signature = request.headers.get("x-slack-signature", "")
        secret = str(self.ctx.secrets.get("signing_secret") or "")
        if not (timestamp.isdigit() and signature and secret):
            raise WebhookAuthError
        if abs(time.time() - int(timestamp)) > MAX_SKEW_SECONDS:
            raise WebhookAuthError  # replayed or badly delayed request
        base = b"v0:" + timestamp.encode() + b":" + request.body
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookAuthError

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        self._verify(request)  # always first: nothing is parsed before this passes
        try:
            payload: dict[str, Any] = json.loads(request.body)
        except ValueError as exc:
            raise WebhookPayloadError("invalid JSON") from exc

        # Slack's one-time endpoint ownership check.
        if payload.get("type") == "url_verification":
            return WebhookResult(
                response={"challenge": str(payload.get("challenge", ""))}, status_code=200
            )

        event = payload.get("event") or {}
        text = str(event.get("text") or "").strip()
        # Ignore bot messages (including our own replies) and non-message events.
        if event.get("type") not in ("app_mention", "message") or event.get("bot_id") or not text:
            return WebhookResult(status_code=200)

        channel, ts = str(event.get("channel", "")), str(event.get("ts", ""))
        user = str(event.get("user") or "") or None
        return WebhookResult(
            messages=[
                InboundMessage(
                    external_id=str(payload.get("event_id") or f"{channel}:{ts}"),
                    subject=single_line(text, 120),
                    body=text,
                    sender_kind="slack" if user else None,
                    sender_id=user,
                    # Replies in a Slack thread land on the same ticket.
                    thread_id=f"{channel}:{event.get('thread_ts') or ts}",
                )
            ],
            status_code=200,
        )

    async def send(self, message: OutboundMessage) -> None:
        token = self.ctx.secrets.get("bot_token")
        if not token or not message.thread_id:
            raise ConnectorError("Slack replies need a bot_token and a thread")
        channel, _, thread_ts = message.thread_id.partition(":")
        url = self.check_outbound_url("https://slack.com/api/chat.postMessage")
        response = await self.ctx.http.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "channel": channel,
                "thread_ts": thread_ts,
                "text": f"*{message.ticket_key}* — {message.body}",
            },
            timeout=15,
        )
        if response.status_code != 200 or not response.json().get("ok"):
            raise ConnectorError(f"Slack API call failed ({response.status_code})")
