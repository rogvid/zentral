# Trial on a VPS

Zentral running on one rented server in about ten minutes — no Entra ID or
other identity provider, no Key Vault or Vault, no DNS setup. Files are in
[`deploy/trial`](https://github.com/rogvid/zentral/tree/main/deploy/trial).

!!! warning "For evaluation with made-up data only"
    A trial makes three trade-offs that a real installation must not:
    people sign in by **picking a persona** (behind one shared password),
    the encryption keys are **plain text in `.env`**, and the server runs
    in **development mode**, so the production start-up checks are off.
    When you are ready for real data, [move to a real deployment](#moving-on).

```text
internet ──443──▶ caddy ──▶ api ──┐        caddy: HTTPS + trial password on sign-in
                         worker ──┴──▶ db
```

## Prerequisites

- A Linux VPS with 2 GB RAM (4 GB if you build the image on it), Docker
  Engine 24+ with the Compose plugin, `git` and `openssl`
- Ports **80 and 443** open to the internet — Caddy uses them to get a
  certificate (check your provider's firewall as well as `ufw`)

## Start

```bash
git clone https://github.com/rogvid/zentral && cd zentral/deploy/trial
./setup.sh
```

`setup.sh` detects the server's public IP and uses `<ip>.sslip.io` as the
hostname — a public DNS service that resolves that name to that IP, so a
real Let's Encrypt certificate works without touching DNS. Have your own
name pointing at the server? Use it instead: `./setup.sh desk.example.com`.

It then writes `.env` (fresh keys, database password and trial password),
builds the image, starts the stack and runs the pre-flight check. At the end
it prints:

```text
  URL       https://203.0.113.7.sslip.io
  Sign-in   user: trial   password: 3kQ…
```

**Save the password** — only its hash is stored. Then fill the desk with
sample teams, systems and tickets:

```bash
docker compose run --rm seed
```

Open the URL, enter the trial password when asked, and pick who to be:
*Ada Admin* sets things up, *Arne Agent* works the queue, *Aud Auditor* sees
only the audit trail, and so on. Open a second browser (or a private window)
to be two people at once.

## How the trial is protected

The persona picker lets whoever reaches it be anyone, administrator
included. Caddy therefore asks for the trial password on the two paths that
hand out sign-ins — `/dev-idp/authorize` and `/api/v1/dev/token` — and on
nothing else: the web client, the API, MCP and webhooks already refuse every
request without a valid token, and those two paths are the only source of
tokens in a trial.

The rest of the hardening of the [Compose deployment](deploy-compose.md)
applies unchanged: unprivileged, read-only containers, a database on an
internal network with no route outside, and only Caddy publishing ports.

## Operating it

| Task | Command (in `deploy/trial`) |
|---|---|
| Logs | `docker compose logs -f api worker caddy` |
| Status | `docker compose ps` |
| Pre-flight | `docker compose run --rm --no-deps api doctor` |
| Stop / start | `docker compose stop` / `docker compose up -d` |
| Update to the latest code | `git pull && docker compose up -d --build` |
| Remove everything | `docker compose down -v && rm .env` |

Restarting the `api` container signs everyone out: the persona sign-in signs
with a key that lives only in memory. Sign in again; nothing else is lost.

**Small VPS?** Building the image needs about 2 GB of memory. Use the released
image instead: set `ZENTRAL_IMAGE=ghcr.io/rogvid/zentral@sha256:<digest>` in
`.env` (from the release notes) and start with `docker compose up -d` (no
`--build`).

**Try on-prem AI** by pointing the worker at an OpenAI-compatible model server
you already run, in `.env`:

```bash
ZENTRAL_AI_PROVIDER=openai_compatible
ZENTRAL_AI_BASE_URL=http://your-model-server:11434/v1
ZENTRAL_AI_MODEL=llama3.1:8b
```

then `docker compose up -d worker`. See
[Switch to on-prem AI](../runbooks/switch-to-on-prem-ai.md).

## Troubleshooting

| Symptom | Cause |
|---|---|
| Browser warns about the certificate, `docker compose logs caddy` shows ACME errors | Port 80 or 443 is not reachable from the internet, or (with your own name) DNS does not point at the server yet. Fix it and `docker compose restart caddy`. |
| `setup.sh`: "Could not detect this host's public IPv4 address" | The server has no outbound internet or only IPv6. Pass a hostname. |
| Signed out after a while | Restart of `api`, or the 8-hour token lifetime. Sign in again. |
| Anything else | [Troubleshooting runbook](../runbooks/troubleshooting.md) |

## Moving on

The trial's data is not meant to be kept: its keys were never escrowed and
persona accounts do not exist in a directory. For real use, start a fresh
[Compose deployment](deploy-compose.md) with your identity provider
([Entra ID](identity-entra.md) or [any OIDC provider](identity-oidc.md)) and a
[key provider](key-custody.md), and follow the
[first-deployment runbook](../runbooks/first-deployment.md). Remove the trial
with `docker compose down -v` once you are done with it.
