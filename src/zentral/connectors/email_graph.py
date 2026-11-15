"""E-mail connector for Microsoft 365 shared mailboxes (Microsoft Graph).

Uses the OAuth 2.0 client-credentials flow with an app registration that has
the *application* permissions ``Mail.Read`` and ``Mail.Send``. Restrict the app
to the service-desk mailbox with an Exchange *RBAC for Applications* scope (or
an application access policy) — otherwise the permission is tenant-wide. See
docs/guides/connectors/email.md.

Inbound: polls the mailbox folder for messages newer than the stored cursor.
Bodies are requested as plain text; no HTML is ever stored.
Outbound: sends replies from the same mailbox with the ticket key in the subject.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from zentral.connectors.base import (
    Capability,
    Connector,
    ConnectorError,
    InboundMessage,
    OutboundMessage,
    PollResult,
)
from zentral.schemas import Email

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
_EPOCH = "1970-01-01T00:00:00Z"
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?Z")

# (tenant, client_id) -> (access_token, monotonic expiry)
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


class GraphMailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mailbox: Email
    folder: str = Field(default="inbox", pattern=r"^[A-Za-z0-9_-]{1,120}$")
    page_size: int = Field(default=25, ge=1, le=50)


class GraphMailConnector(Connector):
    name: ClassVar[str] = "email_graph"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.POLL, Capability.SEND})
    config_model: ClassVar[type[BaseModel]] = GraphMailConfig
    secret_fields: ClassVar[frozenset[str]] = frozenset({"tenant_id", "client_id", "client_secret"})
    required_secrets: ClassVar[frozenset[str]] = secret_fields

    config: GraphMailConfig

    async def _token(self) -> str:
        s = self.ctx.secrets
        key = (str(s["tenant_id"]), str(s["client_id"]))
        cached = _token_cache.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        try:
            resp = await self.ctx.http.post(
                f"{LOGIN}/{key[0]}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": key[1],
                    "client_secret": s["client_secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"token request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"token endpoint returned {resp.status_code}")
        body = resp.json()
        token = str(body["access_token"])
        _token_cache[key] = (
            token,
            time.monotonic() + max(60, int(body.get("expires_in", 300)) - 60),
        )
        return token

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token()}", **kwargs.pop("headers", {})}
        try:
            resp = await self.ctx.http.request(
                method, url, headers=headers, timeout=30, follow_redirects=False, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Graph request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            # Deliberately do not include the response body: it can contain mail content.
            raise ConnectorError(f"Graph returned {resp.status_code}")
        return resp

    async def poll(self, cursor: dict[str, Any]) -> PollResult:
        since = str(cursor.get("received_after") or _EPOCH)
        if not _TIMESTAMP.fullmatch(since):  # the value is interpolated into an OData filter
            since = _EPOCH
        resp = await self._request(
            "GET",
            f"{GRAPH}/users/{self.config.mailbox}/mailFolders/{self.config.folder}/messages",
            params={
                "$filter": f"receivedDateTime gt {since}",
                "$orderby": "receivedDateTime asc",
                "$top": str(self.config.page_size),
                "$select": "id,internetMessageId,conversationId,subject,from,receivedDateTime,body",
            },
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        messages: list[InboundMessage] = []
        latest = since
        for item in resp.json().get("value", []):
            received = str(item.get("receivedDateTime") or "")
            address = ((item.get("from") or {}).get("emailAddress")) or {}
            sender = str(address.get("address") or "").strip() or None
            messages.append(
                InboundMessage(
                    external_id=str(item.get("internetMessageId") or item["id"]),
                    subject=str(item.get("subject") or "(no subject)"),
                    body=str((item.get("body") or {}).get("content") or ""),
                    sender_kind="email" if sender else None,
                    sender_id=sender,
                    sender_email=sender,
                    sender_name=str(address.get("name") or "") or None,
                    thread_id=str(item.get("conversationId") or "") or None,
                    received_at=_parse(received),
                )
            )
            latest = max(latest, received)
        return PollResult(messages=messages, cursor={"received_after": latest})

    async def send(self, message: OutboundMessage) -> None:
        if not message.recipient_email:
            raise ConnectorError("requester has no e-mail address")
        await self._request(
            "POST",
            f"{GRAPH}/users/{self.config.mailbox}/sendMail",
            json={
                "message": {
                    "subject": f"[{message.ticket_key}] {message.subject}",
                    "body": {"contentType": "Text", "content": message.body},
                    "toRecipients": [{"emailAddress": {"address": message.recipient_email}}],
                },
                "saveToSentItems": True,
            },
        )


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
