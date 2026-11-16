# REST API

Everything the web client does goes through this API; there are no private
endpoints.

- Base path: `/api/v1`
- OpenAPI schema: `/api/openapi.json`
- Authentication: `Authorization: Bearer <access token>` on every request
  (except the inbound webhook, which authenticates per channel)
- JSON in, JSON out; unknown fields are rejected

## Conventions

| Status | Meaning |
|---|---|
| `401` | Missing, invalid or expired token. No detail is given. |
| `403` | Authenticated, but a permission is missing: `{"detail":"forbidden","missing_permission":"…"}` |
| `404` | Does not exist **or is outside your team scope** — deliberately indistinguishable |
| `409` | Conflict (duplicate name or key) |
| `413` | Body larger than `ZENTRAL_MAX_BODY_BYTES` |
| `422` | Validation failed. Submitted values are not echoed back. |
| `429` | Rate limit |

Every response has an `X-Request-ID` header; quote it when reporting a problem.

## Tickets

```http
GET    /tickets?status=new&status=open&priority=urgent&system_id=…&q=vpn&limit=50&offset=0
GET    /tickets/stats
POST   /tickets
GET    /tickets/{id}
PATCH  /tickets/{id}
GET    /tickets/{id}/messages
POST   /tickets/{id}/messages
```

List filters: `status` (repeatable), `priority`, `category`, `team_id`,
`system_id`, `no_system`, `assignee_id`, `unassigned`, `requester_email`
(needs `ticket:read_pii`), `q`. `limit` ≤ 100.

```bash
# Create
curl -X POST $BASE/api/v1/tickets -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "subject": "Printer on floor 3 is jammed",
  "body": "Tray 2, error E-204.",
  "priority": "normal",
  "requester_email": "jane.doe@example.com"
}'

# Update — only the fields you send change
curl -X PATCH $BASE/api/v1/tickets/42 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status": "resolved", "system_id": "6f1c…"}'

# Reply (kind: "outbound") or note (kind: "note")
curl -X POST $BASE/api/v1/tickets/42/messages -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"kind": "note", "body": "Replaced the fuser unit."}'
```

A ticket response includes `pii_redacted: true` when your role does not include
`ticket:read_pii`; subject and bodies are then masked and `requester.email` is
`null`.

## Reference data and administration

```http
GET  /me                       GET  /config   (public: sign-in bootstrap)
GET  /teams      POST /teams   DELETE /teams/{id}
POST /teams/{id}/members       DELETE /teams/{id}/members/{user_id}
GET  /systems    POST /systems PUT /systems/{id}   DELETE /systems/{id}
GET  /users
GET  /connectors
GET  /sources    POST /sources PATCH /sources/{id} DELETE /sources/{id}
POST /sources/{id}/rotate-token
GET  /rules      POST /rules   PUT /rules/{id}     DELETE /rules/{id}
```

## Compliance

```http
GET  /audit/events?actor_id=…&action=…&resource_type=…&resource_id=…&outcome=…&before_seq=…&limit=…
POST /audit/verify
POST /privacy/contacts/lookup        {"email": "…"}
GET  /privacy/contacts/{id}/export
POST /privacy/contacts/{id}/erase
```

The audit list is paginated backwards with `before_seq` (use
`next_before_seq` from the previous page).

## Inbound webhook

```http
POST /hooks/{source_id}
```

See [Webhook (services)](connectors/webhook.md).

## Health

`GET /healthz` — the process is alive. `GET /readyz` — the database answers and
its schema matches this version. Neither requires authentication or returns
anything sensitive.

## Getting a token for a script

Use the client-credentials flow of your identity provider with an application
that has been granted a Zentral role — the same as for an
[AI agent](mcp.md#set-up-an-agent-identity).
