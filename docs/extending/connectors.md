# Write a connector

A connector adapts one external channel to Zentral. It is a small Python class
in its own package; Zentral discovers it through an entry point. You never
modify Zentral itself.

The repository contains a complete, tested example:
[`examples/zentral-connector-slack`](https://github.com/rogvid/zentral/tree/main/examples/zentral-connector-slack)
(about 100 lines). Read it alongside this page.

## What a connector does — and does not do

| The connector | Zentral's core |
|---|---|
| Authenticates inbound requests (signature/token) | De-duplication, threading |
| Translates the platform's payload to `InboundMessage` | Requester resolution, system resolution |
| Optionally sends an `OutboundMessage` | Encryption, storage, audit |
| | AI classification, automation rules, retries |

A connector **never touches the database**. This is what keeps third-party code
from bypassing a security control by accident.

## Capabilities

Declare any combination:

| Capability | You implement | Called |
|---|---|---|
| `WEBHOOK` | `handle_webhook(request) -> WebhookResult` | When the platform POSTs to `/api/v1/hooks/{source_id}` |
| `POLL` | `poll(cursor) -> PollResult` | By the worker, every poll interval |
| `SEND` | `send(message) -> None` | By the worker, when an agent replies |

## Skeleton

```python
from typing import ClassVar
from pydantic import BaseModel, ConfigDict
from zentral.connectors.base import (
    Capability, Connector, InboundMessage, WebhookAuthError,
    WebhookPayloadError, WebhookRequest, WebhookResult,
)


class MyConfig(BaseModel):                 # non-secret settings, validated, shown to admins
    model_config = ConfigDict(extra="forbid")
    workspace: str


class MyConnector(Connector):
    name: ClassVar[str] = "mychannel"      # must equal the entry-point name
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.WEBHOOK})
    config_model: ClassVar[type[BaseModel]] = MyConfig
    secret_fields: ClassVar[frozenset[str]] = frozenset({"signing_secret"})
    required_secrets: ClassVar[frozenset[str]] = frozenset({"signing_secret"})

    async def handle_webhook(self, request: WebhookRequest) -> WebhookResult:
        self._verify(request)              # 1. ALWAYS first. Raise WebhookAuthError.
        payload = self._parse(request)     # 2. Raise WebhookPayloadError if malformed.
        return WebhookResult(messages=[    # 3. Normalise.
            InboundMessage(
                external_id=payload["event_id"],
                subject=payload["text"][:120],
                body=payload["text"],
                sender_kind="mychannel",
                sender_id=payload["user_id"],
                thread_id=payload["conversation_id"],
            )
        ], status_code=200)
```

Register it in your package's `pyproject.toml`:

```toml
[project.entry-points."zentral.connectors"]
mychannel = "my_package:MyConnector"
```

Install the package into Zentral's environment (or image) and allow it:

```bash
ZENTRAL_ENABLED_CONNECTORS=webhook,teams,email_graph,mychannel
```

It now appears under *Administration → Channels → Add channel*, with form
fields generated from `config_model` and `secret_fields`.

## `InboundMessage` fields

| Field | Purpose |
|---|---|
| `external_id` | **Unique per message.** The idempotency key: Zentral ingests each ID once. Use the platform's event ID. |
| `subject`, `body` | Plain text. Convert HTML with `zentral.text.html_to_text`. |
| `sender_kind`, `sender_id` | Namespace and stable ID of the sender (`"email"`, address) / (`"slack"`, user ID). Identifies the requester. |
| `sender_email`, `sender_name` | Optional display data. |
| `thread_id` | The platform's conversation ID. Same ID → same ticket. Passed back to you in `OutboundMessage.thread_id` for replies. |
| `priority` | Optional: `low`, `normal`, `high`, `urgent`. |
| `system` | Optional key of the [system](../guides/systems.md) concerned. |
| `received_at` | Optional timestamp. |

## Security checklist

Your connector's webhook is reachable from the internet with **no other
authentication** in front of it. Before you ship:

- [ ] Verify the signature or token **before parsing anything**.
- [ ] Compare secrets with `hmac.compare_digest` (or
      `zentral.security.crypto.constant_time_equals`).
- [ ] If the platform signs a timestamp, reject stale requests (replay).
- [ ] Raise `WebhookAuthError` for every authentication failure — Zentral turns
      it into a uniform `401`.
- [ ] Ignore the platform's own bot messages, or your replies loop back in.
- [ ] Build outbound URLs through `self.check_outbound_url(url)` and add the
      host to `ZENTRAL_OUTBOUND_HOST_ALLOWLIST`.
- [ ] Use `self.ctx.http` for outbound calls (timeouts, no redirects).
- [ ] Never log payloads or secrets; raise `ConnectorError("short message")`
      — the message is stored in the job's error field, so keep content out.
- [ ] Declare secrets in `secret_fields` so they are stored encrypted and never
      returned by the API.

## Verify your own blast radius

If the platform's permission model is broader than your channel needs, do what
the mail connector does: **check, before reading anything, that the credentials
cannot reach beyond the channel**, and raise `ConnectorError` if they can. Fail
closed, cache the positive result briefly, and re-check periodically.

## Self-test and health

Implement `diagnose()` to return a list of `Diagnostic(name, status, detail)`.
It runs when an administrator presses **Test** on the channel. Keep `detail`
free of secrets and content. Polling failures are shown on the channel
automatically from your `ConnectorError` message, so write messages an
administrator can act on.

To do something to a message *after* it is safely stored (mark as read,
acknowledge), return opaque references in `PollResult.ack` and implement
`acknowledge(refs)`.

## Polling connectors

Return new messages and an opaque cursor; Zentral stores the cursor and hands
it back next time. Because ingestion is idempotent on `external_id`, it is
safe to overlap. See `src/zentral/connectors/email_graph.py`.

```python
async def poll(self, cursor: dict) -> PollResult:
    since = cursor.get("since", "1970-01-01T00:00:00Z")
    items = await self._fetch(since)
    return PollResult(messages=[...], cursor={"since": newest_timestamp})
```

## Testing

Test through the real HTTP endpoint with the fixtures in `tests/conftest.py`,
as `tests/test_plugins.py` does: sign a request, post it, assert on the ticket;
then forge, tamper and replay, and assert `401`.
