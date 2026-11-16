# Local setup

## Tools

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- Optional: PostgreSQL 14+ (to run the suite against it), Docker

## Backend

```bash
uv sync --all-groups --all-extras      # runtime + dev + docs + anthropic extra
uv run zentral init --auth dev         # .env with fresh keys, SQLite, dev sign-in
uv run zentral db upgrade
uv run zentral serve                   # http://localhost:8000
uv run zentral worker                  # second terminal
uv run python scripts/seed_demo.py     # sample data
```

## Web client

For UI work, run Vite's dev server (hot reload); it proxies `/api` and `/mcp`
to the backend on port 8000:

```bash
cd web
npm ci
npm run dev                            # http://localhost:5173
```

Because the browser now talks to port 5173, tell the backend that this is its
public address (it is used as the development identity provider's issuer), then
restart `zentral serve`:

```bash
# .env
ZENTRAL_PUBLIC_BASE_URL=http://localhost:5173
```

To serve the built client from the backend instead (public URL back to port
8000):

```bash
npm run build
echo "ZENTRAL_STATIC_DIR=web/dist" >> ../.env
```

!!! note "No inline styles or scripts"
    The Content-Security-Policy forbids them. Use CSS classes; never
    `style={…}` or `dangerouslySetInnerHTML`. The browser walkthrough fails on
    any CSP violation.

## Layout

```text
src/zentral/
  config.py            settings + production safety checks
  runtime.py           wiring shared by server, worker, CLI
  cli.py  doctor.py    command line, pre-flight checks
  security/            tokens (IdP profiles), rbac, crypto, redaction, dev IdP issuer
  audit/               hash-chained trail
  db/                  models, column types, audit guard
  migrations/          Alembic
  services/            tickets, ingest, admin, privacy, automation, jobs (worker)
  connectors/          plug-in contract + webhook, teams, email_graph
  ai/                  provider contract, classification service, providers/
  api/                 app factory, middleware, routes/
  mcp_server.py        MCP tools
web/                   React + TypeScript client
tests/                 pytest suite
examples/              example connector plug-in
deploy/                compose, kubernetes, entra
docs/                  this site
scripts/               seed_demo.py, gen_docs.py
```

Rule of thumb: **routes and MCP tools contain no logic** — they parse input,
call a service function with a `Ctx`, commit, and return. Authorisation and
audit live in `services/`.

## Common tasks

| Task | Command |
|---|---|
| Format, lint | `uv run ruff format . && uv run ruff check .` |
| Type-check | `uv run mypy` |
| Tests | `uv run pytest` |
| Tests on PostgreSQL | `ZENTRAL_TEST_DATABASE_URL=postgresql+asyncpg://… uv run pytest` |
| Security lint | `uv run bandit -q -r src -c pyproject.toml` |
| Dependency audit | `uv run pip-audit` · `npm audit` (in `web/`) |
| New migration | `uv run alembic revision --autogenerate -m "…"` then review by hand |
| Regenerate reference tables | `uv run python scripts/gen_docs.py` |
| Docs preview | `uv run mkdocs serve` |
| Web tests, build | `npm test`, `npm run build` (in `web/`) |

## Database migrations

1. Change the models in `db/models.py`.
2. `uv run alembic revision --autogenerate -m "describe the change"`.
3. **Edit the generated file**: name constraints explicitly, use
   `op.batch_alter_table` for column changes (keeps SQLite working), and make
   the change additive where possible so the previous version still runs.
4. `uv run pytest tests/test_ops.py` — fails if models and migrations differ.
5. Run the suite on PostgreSQL.
