---
name: zentral-agents
description: How zentral is built, checked and deployed, and where it differs from Rogvi's web-app-stack defaults. Read before changing the image, the deploy, sign-in or Sentry.
---

# Zentral

A privacy-first service desk: FastAPI in `src/zentral`, a Vite React client in `web/`, one image for the server, the worker and migrations.

## Checks

- Backend: `uv run ruff format --check src tests scripts`, `uv run ruff check src tests scripts`, `uv run mypy`, `uv run pytest`.
- Generated docs: `uv run python scripts/gen_docs.py` after changing a setting.
- Web, from `web/`: `npm test`, `npm run build`.
- Browser end to end: `scripts/e2e.sh`.

## Deploying

There are two deploy paths, and they share the image.

- **The webhost** (`https://zentral.kvist.dev` on vps-1): `.github/workflows/deploy.yml` builds `ghcr.io/rogvid/zentral:sha-<commit>` after CI passes on `main` and runs `deploy zentral sha-<commit>` over SSH.
  The server's compose file, `app.env`, Caddy site and dump hook live in the provisioning repo (`hosts/mise.vps-1.toml`), and secrets in its sops file.
- **`deploy/compose` and `zentralctl`**: for other operators on their own server, with their own Caddy, Keycloak and backups.
  Do not make it depend on the webhost, or the reverse.

The encryption keys live in the provisioning repo's sops file for vps-1.
A database dump cannot be read without them, so Rogvi keeps a copy in his password manager.

## Stack choices

Where zentral differs from the `web-app-stack` skill, with the reason.

- Layout: `src/zentral` and `web/` instead of `backend/` and `frontend/`, because the project predates the webhost and its packaging depends on it.
- Tasks: `Makefile` and `uv`, no `mise.toml` yet, because the project predates the convention.
- Logging: `structlog` instead of `loguru`, because its processors redact personal data from every log line.
- Settings: every variable has the `ZENTRAL_` prefix (`ZENTRAL_ENV`, `ZENTRAL_SENTRY_DSN`, `ZENTRAL_SENTRY_RELEASE`), because pydantic-settings reads the whole configuration under that prefix.
- Port: the container listens on 8000 instead of 8080, because the image and `deploy/compose` already use it.
- Health: the deploy waits on `/readyz` (database and schema version) so a bad migration rolls back, and uptime checks use `/healthz`, instead of `/api/health`.
- Services: `server`, `worker`, a one-shot `migrate` and `db` from one image, because background jobs need their own process and a failed migration must stop both.
- Deploy trigger: after CI passes on `main` (`workflow_run`) instead of on every push, so a commit that fails CI is never deployed.
- Sign-in (temporary): dev auth behind `ZENTRAL_DEV_AUTH_PASSWORD` in production, because Rogvi wants something simple for testing until real OIDC sign-in comes; everyone with the password can be any persona, including admin, so it must hold no real personal data.
- Sessions: one server process, and every restart or deploy signs everyone out, because the dev sign-in's signing key is per process and in memory.
- Keys: plain-text keys from sops with `ZENTRAL_ALLOW_ENV_KEYS=true`, because vps-1 has no KMS.
- Sentry messages: exception messages, request paths and headers are dropped as well, because in a service desk they can quote ticket content, and SQL errors quote their parameters.
- Sentry integrations: auto-enabled integrations are off on the server, because the database, HTTP client, MCP and AI ones would record statements, webhook URLs, tool arguments and prompts.
- Sentry in the browser: errors only, with no tracing and no Session Replay, because every screen shows tickets and names.
