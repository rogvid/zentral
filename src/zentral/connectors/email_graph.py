"""E-mail connector for Microsoft 365 shared mailboxes (Microsoft Graph).

Scoped by construction
----------------------
Graph *application* permissions are tenant-wide unless the administrator
restricts them. A service-desk connector that could read every mailbox in the
organisation would be an unacceptable blast radius, so this connector does not
merely document the restriction — it **verifies it and refuses to run without
it**:

1. The access token Microsoft issues is inspected. If it carries no tenant-wide
   ``Mail.*`` role, access can only come from an Exchange *RBAC for
   Applications* scope, which is limited to the mailboxes named there.
2. If it does carry tenant-wide roles (the app relies on an *application
   access policy* instead), a **probe mailbox** must be configured: an address
   the connector must *not* be able to read. The connector tries, and proceeds
   only if Exchange denies it.

The check is repeated hourly, so a later widening of permissions stops the
channel instead of silently extending its reach. There is no setting to turn
it off.

Configurable
------------
Folder, first-sync behaviour, sender allow/block lists, auto-reply handling,
what happens to mail after ingestion, whether and to whom replies are sent —
see ``GraphMailConfig``.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import quote

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from zentral.connectors.base import (
    Capability,
    Connector,
    ConnectorError,
    ConnectorRejected,
    Diagnostic,
    InboundAttachment,
    InboundMessage,
    OutboundMessage,
    PollResult,
)

# Mailbox addresses end up in a Graph URL path, so they are held to a strict
# character set (no "/", "?", "#", "%" …) and percent-encoded when used.
Mailbox = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        max_length=320,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+'-]{0,63}@[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$",
    ),
]

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?Z")
_DOMAIN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)

# Entra application roles that grant mail access to *every* mailbox in the tenant.
TENANT_WIDE_MAIL_ROLES = frozenset(
    {
        "Mail.Read",
        "Mail.ReadBasic",
        "Mail.ReadBasic.All",
        "Mail.ReadWrite",
        "Mail.Send",
        "full_access_as_app",
    }
)
SCOPE_RECHECK_SECONDS = 3600

# (tenant, client_id) -> (access_token, monotonic expiry)
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
# (tenant, client_id, mailbox, probe) -> monotonic time the scope was last proven
_scope_verified: dict[tuple[str, str, str, str], float] = {}


class ScopeError(ConnectorError):
    """The connector's credentials are not provably limited to its mailbox."""


def _domains(value: Any) -> Any:
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    if isinstance(value, list):
        return [str(v).strip().lower().lstrip("@") for v in value]
    return value


class GraphMailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mailbox: Mailbox = Field(
        title="Mailbox",
        description="Address of the shared mailbox Zentral reads, e.g. servicedesk@example.com.",
    )
    folder: str = Field(
        default="inbox",
        pattern=r"^[A-Za-z0-9_-]{1,120}$",
        title="Folder",
        description="Well-known folder name or folder ID to read from.",
    )
    scope_probe_mailbox: Mailbox | None = Field(
        default=None,
        title="Scope probe mailbox",
        description=(
            "Another mailbox in your tenant that Zentral must NOT be able to read. Required when "
            "access is restricted with an application access policy; optional with RBAC for "
            "Applications. Zentral tries to read it and only runs if Exchange refuses."
        ),
    )
    initial_sync: Literal["new_only", "all"] = Field(
        default="new_only",
        title="First synchronisation",
        description="new_only: ingest mail that arrives from now on. all: also import everything already in the folder.",
    )
    allowed_sender_domains: list[str] = Field(
        default_factory=list,
        max_length=200,
        title="Allowed sender domains",
        description="Only accept mail from these domains (e.g. example.com). Empty accepts any sender.",
    )
    blocked_senders: list[str] = Field(
        default_factory=list,
        max_length=500,
        title="Blocked senders",
        description="Addresses or domains to ignore, e.g. noreply@example.com, newsletters.example.org.",
    )
    ignore_automatic_replies: bool = Field(
        default=True,
        title="Ignore automatic replies",
        description="Skip out-of-office replies, bounces and bulk mail. Prevents mail loops.",
    )
    after_ingest: Literal["leave", "mark_read"] = Field(
        default="leave",
        title="After ingestion",
        description="leave: do not touch the mailbox (needs Mail.Read only). mark_read: mark ingested mail as read (needs Mail.ReadWrite).",
    )
    send_replies: bool = Field(
        default=True,
        title="Send replies",
        description="Deliver agents' replies from this mailbox (needs Mail.Send). Turn off for a read-only channel.",
    )
    allowed_recipient_domains: list[str] = Field(
        default_factory=list,
        max_length=200,
        title="Allowed recipient domains",
        description="Only send replies to these domains. Empty allows replying to any requester.",
    )
    acknowledge_new_tickets: bool = Field(
        default=True,
        title="Acknowledge new requests",
        description="Reply automatically to each new request with its ticket number, so the requester knows it arrived.",
    )
    acknowledgement_text: str = Field(
        default="",
        max_length=2000,
        title="Acknowledgement text",
        description="Leave empty for the standard text. {ticket_key} is replaced by the ticket number.",
    )
    ingest_attachments: bool = Field(
        default=True,
        title="Keep attached files",
        description=(
            "Store files and pasted screenshots that arrive with a message (when attachments "
            "are enabled on this desk). Each is checked, cleaned and malware-scanned first."
        ),
    )
    save_to_sent_items: bool = Field(
        default=True,
        title="Save replies to Sent Items",
        description="Keep a copy of sent replies in the mailbox.",
    )
    page_size: int = Field(
        default=25, ge=1, le=50, title="Batch size", description="Messages fetched per poll."
    )

    @model_validator(mode="after")
    def _acknowledgement_needs_replies(self) -> GraphMailConfig:
        if self.acknowledge_new_tickets and not self.send_replies:
            raise ValueError("acknowledging new requests needs 'send replies' to be on")
        return self

    _normalise = field_validator(
        "allowed_sender_domains", "allowed_recipient_domains", "blocked_senders", mode="before"
    )(_domains)

    @field_validator("allowed_sender_domains", "allowed_recipient_domains")
    @classmethod
    def _valid_domains(cls, value: list[str]) -> list[str]:
        for domain in value:
            if not _DOMAIN.fullmatch(domain):
                raise ValueError(f"not a domain name: {domain[:60]}")
        return sorted(set(value))

    @field_validator("blocked_senders")
    @classmethod
    def _valid_blocked(cls, value: list[str]) -> list[str]:
        for entry in value:
            domain = entry.rsplit("@", 1)[-1]
            if not _DOMAIN.fullmatch(domain) or entry.count("@") > 1:
                raise ValueError(f"not an address or domain: {entry[:60]}")
        return sorted(set(value))


def _token_roles(token: str) -> frozenset[str] | None:
    """Application roles in a Graph access token, or None if it cannot be read.

    The token is *ours* (just obtained from Microsoft over TLS); we only read
    its claims to learn which permissions the app was granted, we do not use
    this to authenticate anybody.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        roles = claims.get("roles", [])
        return (
            frozenset(r for r in roles if isinstance(r, str)) if isinstance(roles, list) else None
        )
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def _domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def _matches(address: str, entries: list[str]) -> bool:
    """True if the address equals an entry or belongs to a listed domain (or a subdomain)."""
    address, domain = address.lower(), _domain_of(address)
    return any(
        address == entry or domain == entry or domain.endswith("." + entry) for entry in entries
    )


def is_automatic(item: dict[str, Any]) -> bool:
    """Out-of-office replies, bounces and bulk mail (RFC 3834 and common practice)."""
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", "")).lower()
        for h in item.get("internetMessageHeaders") or []
        if isinstance(h, dict)
    }
    return (
        headers.get("auto-submitted", "no") != "no"
        or headers.get("precedence", "") in ("bulk", "junk", "list", "auto_reply")
        or "x-autoreply" in headers
        or "x-autorespond" in headers
        or "list-unsubscribe" in headers
        or headers.get("return-path", "x") == "<>"
    )


class GraphMailConnector(Connector):
    name: ClassVar[str] = "email_graph"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.POLL, Capability.SEND, Capability.SEND_FILES}
    )
    config_model: ClassVar[type[BaseModel]] = GraphMailConfig
    secret_fields: ClassVar[frozenset[str]] = frozenset({"tenant_id", "client_id", "client_secret"})
    required_secrets: ClassVar[frozenset[str]] = secret_fields

    config: GraphMailConfig

    # -- Graph plumbing --------------------------------------------------------------

    def _identity(self) -> tuple[str, str]:
        return str(self.ctx.secrets["tenant_id"]), str(self.ctx.secrets["client_id"])

    async def _token(self) -> str:
        key = self._identity()
        cached = _token_cache.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        try:
            resp = await self.ctx.http.post(
                f"{LOGIN}/{key[0]}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": key[1],
                    "client_secret": self.ctx.secrets["client_secret"],
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
        lifetime = max(60, int(body.get("expires_in", 300)) - 60)
        _token_cache[key] = (token, time.monotonic() + lifetime)
        return token

    async def _raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token()}", **kwargs.pop("headers", {})}
        try:
            return await self.ctx.http.request(
                method, url, headers=headers, timeout=30, follow_redirects=False, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Graph request failed: {type(exc).__name__}") from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        resp = await self._raw(method, url, **kwargs)
        if resp.status_code >= 400:
            # Deliberately do not include the response body: it can contain mail content.
            raise ConnectorError(f"Graph returned {resp.status_code}")
        return resp

    def _folder_url(self, mailbox: str) -> str:
        return (
            f"{self._user_url(mailbox)}/mailFolders/{quote(self.config.folder, safe='')}/messages"
        )

    @staticmethod
    def _user_url(mailbox: str) -> str:
        return f"{GRAPH}/users/{quote(mailbox, safe='@')}"

    # -- scope verification ------------------------------------------------------------

    async def verify_scope(self, *, force: bool = False) -> str:
        """Prove the credentials cannot read beyond this mailbox, or raise ``ScopeError``.

        Returns a short description of how the scope was established.
        """
        probe = self.config.scope_probe_mailbox
        key = (*self._identity(), self.config.mailbox.lower(), (probe or "").lower())
        proven = _scope_verified.get(key)
        if not force and proven and time.monotonic() - proven < SCOPE_RECHECK_SECONDS:
            return "verified within the last hour"

        _scope_verified.pop(key, None)
        roles = _token_roles(await self._token())
        tenant_wide = sorted(roles & TENANT_WIDE_MAIL_ROLES) if roles is not None else None

        if probe:
            if probe.lower() == self.config.mailbox.lower():
                raise ScopeError("scope probe mailbox must be a different mailbox")
            resp = await self._raw(
                "GET",
                f"{self._user_url(probe)}/mailFolders/inbox/messages",
                params={"$top": "1", "$select": "id"},
            )
            if resp.status_code == 200:
                raise ScopeError(
                    "credentials can read the probe mailbox: mail access is not restricted to "
                    "this channel's mailbox"
                )
            if resp.status_code != 403:
                raise ScopeError(
                    f"scope probe was inconclusive (Graph returned {resp.status_code}); the probe "
                    "must be an existing mailbox that Exchange denies"
                )
            how = "Exchange denied access to the probe mailbox"
        elif tenant_wide is None:
            raise ScopeError(
                "could not read the granted permissions; configure a scope probe mailbox"
            )
        elif tenant_wide:
            raise ScopeError(
                f"the app holds tenant-wide permissions ({', '.join(tenant_wide)}). Scope it with "
                "Exchange RBAC for Applications and remove these Graph permissions, or configure "
                "a scope probe mailbox to prove an application access policy applies"
            )
        else:
            how = "no tenant-wide mail permission; access comes from an Exchange RBAC scope"

        _scope_verified[key] = time.monotonic()
        return how

    # -- inbound -----------------------------------------------------------------------

    def _accepts(self, sender: str | None, item: dict[str, Any]) -> bool:
        cfg = self.config
        if sender and sender.lower() == cfg.mailbox.lower():
            return False  # our own sent mail arriving back: never ingest (loop)
        if cfg.ignore_automatic_replies and is_automatic(item):
            return False
        if sender is None:
            return not cfg.allowed_sender_domains
        if _matches(sender, cfg.blocked_senders):
            return False
        return not cfg.allowed_sender_domains or _matches(sender, cfg.allowed_sender_domains)

    async def poll(self, cursor: dict[str, Any]) -> PollResult:
        await self.verify_scope()
        since = str(cursor.get("received_after") or "")
        if not _TIMESTAMP.fullmatch(since):  # also guards the OData filter below
            if self.config.initial_sync == "new_only":
                # First run: remember "now" and import nothing that is already there.
                now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                return PollResult(messages=[], cursor={"received_after": now})
            since = "1970-01-01T00:00:00Z"

        resp = await self._request(
            "GET",
            self._folder_url(self.config.mailbox),
            params={
                "$filter": f"receivedDateTime gt {since}",
                "$orderby": "receivedDateTime asc",
                "$top": str(self.config.page_size),
                "$select": (
                    "id,internetMessageId,conversationId,subject,from,receivedDateTime,body,"
                    "internetMessageHeaders,hasAttachments"
                ),
            },
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        messages: list[InboundMessage] = []
        ack: list[str] = []
        latest = since
        for item in resp.json().get("value", []):
            received = str(item.get("receivedDateTime") or "")
            if _TIMESTAMP.fullmatch(received):
                latest = max(latest, received)
            address = ((item.get("from") or {}).get("emailAddress")) or {}
            sender = str(address.get("address") or "").strip() or None
            if not self._accepts(sender, item):
                continue
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
                    attachments=await self._attachments(item),
                )
            )
            if self.config.after_ingest == "mark_read" and item.get("id"):
                ack.append(str(item["id"]))
        return PollResult(messages=messages, cursor={"received_after": latest}, ack=ack)

    async def _attachments(self, item: dict[str, Any]) -> tuple[InboundAttachment, ...]:
        """Files on a message, including screenshots pasted into the body (inline)."""
        settings = self.ctx.settings
        ref = str(item.get("id") or "")
        if not (
            item.get("hasAttachments")
            and self.config.ingest_attachments
            and settings.attachments_enabled
            and re.fullmatch(r"[A-Za-z0-9_=-]{1,300}", ref)
        ):
            return ()
        resp = await self._request(
            "GET",
            f"{self._user_url(self.config.mailbox)}/messages/{ref}/attachments",
            params={"$top": "10"},
        )
        files: list[InboundAttachment] = []
        for att in resp.json().get("value", []):
            # Only plain files: never attached mail items or cloud links.
            if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            if int(att.get("size") or 0) > settings.attachment_max_bytes * 2:
                continue  # too large to keep (checked exactly after decoding)
            try:
                data = base64.b64decode(str(att.get("contentBytes") or ""), validate=True)
            except (ValueError, binascii.Error):
                continue
            if data:
                files.append(InboundAttachment(str(att.get("name") or "attachment"), data))
        return tuple(files)

    async def acknowledge(self, refs: list[str]) -> None:
        for ref in refs:
            if not re.fullmatch(r"[A-Za-z0-9_=-]{1,300}", ref):
                continue
            await self._request(
                "PATCH",
                f"{self._user_url(self.config.mailbox)}/messages/{ref}",
                json={"isRead": True},
            )

    # -- outbound ----------------------------------------------------------------------

    async def send(self, message: OutboundMessage) -> None:
        cfg = self.config
        if not cfg.send_replies:
            raise ConnectorRejected("replies are disabled for this channel")
        if not message.recipient_email:
            raise ConnectorRejected("requester has no e-mail address")
        if cfg.allowed_recipient_domains and not _matches(
            message.recipient_email, cfg.allowed_recipient_domains
        ):
            raise ConnectorRejected("recipient domain is not on this channel's allow-list")
        await self.verify_scope()
        await self._request(
            "POST",
            f"{self._user_url(cfg.mailbox)}/sendMail",
            json={
                "message": {
                    "subject": (
                        f"[{message.ticket_key}] {message.subject}"
                        if message.ticket_key
                        else message.subject
                    ),
                    "body": {"contentType": "Text", "content": message.body},
                    "toRecipients": [{"emailAddress": {"address": message.recipient_email}}],
                    **(
                        {
                            "attachments": [
                                {
                                    "@odata.type": "#microsoft.graph.fileAttachment",
                                    "name": a.filename,
                                    "contentType": a.content_type,
                                    "contentBytes": base64.b64encode(a.data).decode(),
                                }
                                for a in message.attachments
                            ]
                        }
                        if message.attachments
                        else {}
                    ),
                },
                "saveToSentItems": cfg.save_to_sent_items,
            },
        )

    # -- self-test ---------------------------------------------------------------------

    async def diagnose(self) -> list[Diagnostic]:
        results: list[Diagnostic] = []
        try:
            token = await self._token()
        except ConnectorError as exc:
            return [Diagnostic("Sign in to Microsoft", "fail", str(exc))]
        results.append(Diagnostic("Sign in to Microsoft", "ok", "client credentials accepted"))

        try:
            results.append(Diagnostic("Mailbox scope", "ok", await self.verify_scope(force=True)))
        except ConnectorError as exc:
            results.append(Diagnostic("Mailbox scope", "fail", str(exc)))

        resp = await self._raw(
            "GET", self._folder_url(self.config.mailbox), params={"$top": "1", "$select": "id"}
        )
        if resp.status_code == 200:
            results.append(
                Diagnostic("Read mailbox", "ok", f"folder '{self.config.folder}' is readable")
            )
        else:
            hint = {
                403: "permission missing or not yet propagated",
                404: "mailbox or folder not found",
            }
            results.append(
                Diagnostic(
                    "Read mailbox",
                    "fail",
                    f"Graph returned {resp.status_code}: {hint.get(resp.status_code, 'unexpected')}",
                )
            )

        roles = _token_roles(token) or frozenset()
        if not self.config.send_replies:
            results.append(
                Diagnostic("Send replies", "ok", "disabled for this channel (read-only)")
            )
        else:
            results.append(
                Diagnostic(
                    "Send replies",
                    "warn",
                    "cannot be verified without sending a message; reply on a test ticket to confirm",
                )
            )
        if (
            self.config.after_ingest == "mark_read"
            and "Mail.Read" in roles
            and "Mail.ReadWrite" not in roles
        ):
            results.append(
                Diagnostic(
                    "Mark as read", "warn", "needs Mail.ReadWrite; the app has Mail.Read only"
                )
            )
        return results


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
