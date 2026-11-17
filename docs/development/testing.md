# Testing

```bash
uv run pytest                                   # ~20 s, in-memory SQLite
ZENTRAL_TEST_DATABASE_URL=postgresql+asyncpg://zentral:zentral@localhost/zentral_test uv run pytest
(cd web && npm test)
```

## Philosophy

**Test through the front door.** Almost every test drives the real application
over HTTP with a real (locally issued, RS256-signed) token: real token
validation, real encryption, real audit chain, real database. Only the network
beyond Zentral is replaced. A test that passes means the feature works for a
client, not that a function returns what a mock was told to return.

**Security properties are tests.** If the documentation claims a property,
there should be a test that would fail if it stopped being true — see the table
in [Security posture](../compliance/security-posture.md). When you add a
control, add the test that attacks it.

**Assert on absence.** Many tests search for what must *not* be there: plain
text in the database, content in the audit trail, personal data in a masked
response.

## Fixtures (`tests/conftest.py`)

| Fixture | Gives you |
|---|---|
| `client` | An HTTP client bound to the full app |
| `auth("agent")` | An `Authorization` header for a role: `admin`, `agent`, `agent2`, `viewer`, `auditor`, `privacy`, `bot` (Automation), `ingest`. Extra claims as keyword arguments |
| `issuer` | The local token issuer — craft arbitrary (including invalid) tokens |
| `worker` | `await worker.run_once()` processes queued jobs deterministically |
| `outbound` | Records and answers outbound HTTP (`outbound.on("graph.microsoft.com", handler)`); anything unexpected returns an error |
| `runtime` | The wired runtime; swap `runtime.ai` for a fake provider |
| `db` | A session for direct database assertions |
| `as_db_superuser` | A session with the audit triggers disabled, to test the hash chain as an attacker with table-owner rights |

Helpers in `tests/helpers.py`: `make_team`, `make_ticket`, `make_source`,
`audit_events`, `mcp_call`.

## Writing a test

```python
async def test_viewer_cannot_reply(client, auth):
    team = await make_team(client, auth, "Support", ["viewer"])
    ticket = await make_ticket(client, auth, team_id=team, requester_email="jane@example.com")

    r = await client.post(f"/api/v1/tickets/{ticket['id']}/messages",
                          headers=auth("viewer"), json={"kind": "outbound", "body": "hi"})

    assert r.status_code == 403
    denied = await audit_events(client, auth, outcome="denied")
    assert denied[0]["details"] == {"permission": "ticket:reply"}
```

Checklist for a new endpoint or tool:

- [ ] happy path,
- [ ] each role that must **not** be able to use it (add to `FORBIDDEN` in
      `test_rbac.py`),
- [ ] out-of-team-scope behaviour,
- [ ] invalid input (and that unknown fields are rejected),
- [ ] the audit event, and that it contains no content,
- [ ] the same through MCP if a tool exposes it.

## Both databases

SQLite keeps the default run fast and dependency-free; PostgreSQL is what
production uses. CI runs both. Some behaviour exists only on PostgreSQL
(append-only triggers, advisory lock, `SKIP LOCKED`) and those tests skip on
SQLite.

## Live AI test (opt-in)

Calls a real model; never runs by default.

```bash
ZENTRAL_LIVE_AI=1 uv run pytest tests/test_ai.py -m live_ai                     # claude CLI, Haiku
ZENTRAL_LIVE_AI=1 ZENTRAL_LIVE_AI_PROVIDER=anthropic uv run pytest … -m live_ai # API key
ZENTRAL_LIVE_AI=1 ZENTRAL_LIVE_AI_PROVIDER=openai_compatible \
  ZENTRAL_AI_BASE_URL=http://localhost:11434/v1 ZENTRAL_LIVE_AI_MODEL=llama3.1:8b uv run pytest … -m live_ai
```

Use it to compare models on the task before choosing one.

## Web client

Unit tests (`npm test`) cover pure helpers: routing, formatting, PKCE, theme,
schema-driven forms.

Behaviour is covered by a browser end-to-end test:

```bash
scripts/e2e.sh                 # or: make e2e
scripts/e2e.sh ./screenshots   # also save screenshots
```

It builds the client, starts a throw-away development instance with sample
data, and runs `web/e2e/walkthrough.mjs`: sign-in as each persona through the
real OpenID Connect flow, then the main journeys — working a ticket, tags,
drafts, activity, creating a ticket, adding/testing/editing/deleting a mail
channel, toggling a rule, verifying the audit trail, the masked agent view, a
privacy export and erasure, and the phone layout in dark mode.

It **fails on any console error (including CSP violations), any uncaught page
error and any unexpected HTTP error**, so add a step whenever you add a
journey. Deliberately provoked errors are declared with `expectError(...)`.
It has already paid for itself: it found an auditor landing on a "no access"
page, a race that wiped text typed while a message was being sent, and form
hints that polluted accessible names.

## What CI runs

Formatting · lint · types · `bandit` · tests on SQLite with coverage · tests on
PostgreSQL · `pip-audit` · web tests, build, `npm audit` · browser end-to-end
test · generated docs are
current · docs build in strict mode · image build and container smoke test ·
Compose file validation.
