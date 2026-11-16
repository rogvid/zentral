# Deploy with Docker Compose

A complete installation on one host: TLS proxy, API, worker and PostgreSQL —
optionally an on-prem model server. Files are in
[`deploy/compose`](https://github.com/rogvid/zentral/tree/main/deploy/compose).

```text
internet ──443──▶ caddy ──▶ api ──┐
                         worker ──┴──▶ db        (backend network: no route outside)
```

## Prerequisites

- A Linux host with Docker Engine 24+ and the Compose plugin
- A DNS name pointing at the host (`servicedesk.example.com`), ports 80 and 443
  reachable (for automatic certificates; see below for an internal CA)
- Your identity provider set up: [Entra ID](identity-entra.md) or
  [OIDC](identity-oidc.md)

## 1. Get the files and build the image

```bash
git clone https://github.com/rogvid/zentral && cd zentral
docker build -t zentral:local .
cd deploy/compose
```

(Or pull a released image and set `ZENTRAL_IMAGE` in `.env`.)

## 2. Configure

```bash
cp .env.example .env && chmod 600 .env
docker run --rm zentral:local keys generate >> .env    # three keys
$EDITOR .env
```

Fill in:

| Variable | Value |
|---|---|
| `ZENTRAL_HOSTNAME`, `ZENTRAL_PUBLIC_BASE_URL`, `ZENTRAL_ALLOWED_HOSTS` | Your DNS name |
| `POSTGRES_PASSWORD` | A long random string (`openssl rand -base64 32`) |
| Identity block | Tenant and client IDs, or OIDC issuer/audience/client |

`keys generate` appended three lines at the end; remove the three empty
placeholders above them. **Copy the three keys to your password manager now.**

## 3. Start

```bash
docker compose up -d
docker compose ps                      # migrate: exited (0); others: running/healthy
docker compose run --rm api doctor     # pre-flight
```

Open `https://servicedesk.example.com` and sign in.

## 4. First-time setup in the application

Continue with the [first-deployment runbook](../runbooks/first-deployment.md#in-the-application).

## Operating it

| Task | Command |
|---|---|
| Logs | `docker compose logs -f api worker` |
| Status | `docker compose ps` |
| Pre-flight | `docker compose run --rm api doctor` |
| Verify audit trail | `docker compose run --rm api audit verify` |
| Stop / start | `docker compose stop` / `docker compose up -d` |
| Upgrade | [Upgrade runbook](../runbooks/upgrade.md) |
| Backup | [Backup runbook](../runbooks/backup-restore.md) |

## Variations

### On-prem AI on the same host

```bash
docker compose --profile ai up -d
docker compose exec ollama ollama pull llama3.1:8b
```

Then in `.env`:

```bash
ZENTRAL_AI_PROVIDER=openai_compatible
ZENTRAL_AI_BASE_URL=http://ollama:11434/v1
ZENTRAL_AI_MODEL=llama3.1:8b
```

`docker compose up -d worker` and check `doctor`. The model server is only
reachable on the internal network. For GPU use, add your runtime's device
reservation to the `ollama` service.

### Internal CA or existing certificates

Edit `Caddyfile`:

```text
{$ZENTRAL_HOSTNAME} {
    tls /certs/fullchain.pem /certs/key.pem
    reverse_proxy api:8000 { header_up X-Forwarded-For {remote_host} }
}
```

and mount the certificate directory into the `caddy` service.

### External PostgreSQL

Remove the `db` service and its `depends_on` entries, and replace the
`ZENTRAL_DATABASE_URL` line in the `x-zentral` block with your server's URL
(`postgresql+asyncpg://user:pass@host:5432/zentral?ssl=require`).

### Behind an existing reverse proxy

Remove the `caddy` service, publish the API on localhost only
(`ports: ["127.0.0.1:8000:8000"]`), and set `--forwarded-allow-ips` to your
proxy's address. Your proxy must: terminate TLS, pass `Host` unchanged, set
`X-Forwarded-For`/`X-Forwarded-Proto`, and limit request bodies to 1 MiB.

## What the stack already does for you

- Containers run as uid 10001, read-only root filesystem, all capabilities
  dropped, `no-new-privileges`.
- The database is on an `internal` network: not published, no outbound route.
- Migrations run to completion before the API and worker start.
- Only Caddy publishes ports; it never forwards a client-supplied
  `X-Forwarded-For`.

## What it does not do

Backups, log shipping, host patching and monitoring are yours — see
[Monitoring](monitoring.md) and the [runbooks](../runbooks/index.md). A single
host is a single point of failure.
