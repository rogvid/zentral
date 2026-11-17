# Getting started

This gets you a local instance with sample data in about five minutes. It uses
SQLite and the built-in **development sign-in** — fine for a laptop, never for
real data. For a real installation go to [Deployment](operations/deployment.md).

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ (only to build the web client)

## 1. Install and configure

```bash
git clone https://github.com/rogvid/zentral && cd zentral
uv sync
(cd web && npm ci && npm run build)

uv run zentral init --auth dev          # writes .env with freshly generated keys
echo "ZENTRAL_STATIC_DIR=web/dist" >> .env
uv run zentral db upgrade               # creates the SQLite database
uv run zentral doctor                   # pre-flight check
```

`zentral doctor` should end with `0 failure(s)`. The warning about development
sign-in is expected.

## 2. Run

Two processes: the server and the background worker.

```bash
uv run zentral serve     # terminal 1 — http://localhost:8000
uv run zentral worker    # terminal 2 — AI triage, polling, delivery, retention
```

## 3. Add sample data

```bash
uv run python scripts/seed_demo.py
```

This creates three teams, five systems, two automation rules, a webhook channel
and six tickets.

For charts worth looking at in **Insights**, add a month of resolved history
(development mode only — it refuses to run otherwise):

```bash
uv run python scripts/seed_history.py
```

## 4. Look around

Open <http://localhost:8000>. The development identity provider asks who you
want to be:

| Persona | Try this |
|---|---|
| **Arne Agent** | Open a ticket, add a note, reply, change the system. |
| **Ada Admin** | *Administration* → systems, channels, automation rules. *Audit trail* → *Verify integrity*. |
| **Triage Bot** | The same inbox as an AI agent sees it: personal data masked. |
| **Pia Privacy** | *Privacy requests* → look up `jane.doe@example.com`, export, erase. |
| **Aud Auditor** | Only the audit trail — no ticket content. |

To switch persona, *Sign out* and pick another.

## 5. Send a ticket from a "service"

Create a channel under *Administration → Channels → Add channel* (connector
*Webhook*). Copy the token shown once, then:

```bash
curl -X POST "http://localhost:8000/api/v1/hooks/<source-id>" \
  -H "Authorization: Bearer zis_…" -H "Content-Type: application/json" \
  -d '{"title": "Checkout returns 500", "description": "Since 09:40.", "severity": "critical", "system": "payments"}'
```

Within a couple of seconds the worker classifies it and the rules route it.

## 6. Try AI triage with a real model

By default the classifier is a deterministic keyword matcher (`rules`). To
evaluate a language model before you have on-prem hardware, you can use Claude
Haiku — either with an API key, or with a Claude subscription through the
`claude` CLI:

=== "Claude subscription (CLI)"

    ```bash
    # .env
    ZENTRAL_AI_PROVIDER=claude_cli
    ZENTRAL_AI_MODEL=claude-haiku-5-5
    ```

=== "Anthropic API key"

    ```bash
    uv sync --extra anthropic
    # .env
    ZENTRAL_AI_PROVIDER=anthropic
    ZENTRAL_AI_MODEL=claude-haiku-5-5
    ANTHROPIC_API_KEY=sk-ant-…
    ```

=== "On-prem (Ollama)"

    ```bash
    ollama pull llama3.1:8b
    # .env
    ZENTRAL_AI_PROVIDER=openai_compatible
    ZENTRAL_AI_BASE_URL=http://localhost:11434/v1
    ZENTRAL_AI_MODEL=llama3.1:8b
    ```

Restart the worker and run `uv run zentral doctor` — the *AI provider* line
shows a live classification.

!!! warning "Cloud models and real data"
    `anthropic` and `claude_cli` send (masked) ticket text to a cloud service.
    Use them with test data. For personal data, follow
    [Switch to on-prem AI](runbooks/switch-to-on-prem-ai.md). `claude_cli` is
    refused in production.

## Next

- [Connect an AI agent over MCP](guides/mcp.md)
- [Deploy for real](operations/deployment.md)
