# Deploy on a single VPS

The [Docker Compose deployment](deploy-compose.md) on one rented server, for
when you have **no Entra ID, other identity provider or KMS** of your own. One
command sets it up in production mode — with all start-up safety checks on —
and adds what such a server otherwise lacks:

| | |
|---|---|
| **Sign-in** | A bundled [Keycloak](https://www.keycloak.org/), configured for Zentral: email + password + **authenticator app (required for everyone)**, lock-out after repeated failures, password policy. Its admin console is not reachable from the internet. |
| **People and AI agents** | Managed with `./zentralctl user …` and `./zentralctl agent …` |
| **HTTPS** | Automatic certificates; with no DNS name of your own, `<ip>.sslip.io` works out of the box |
| **Backups** | Database dumps every day, kept 14 days, plus a one-command restore drill |
| **Operations** | `./zentralctl status · logs · upgrade · backup · restore · drill` |

```text
internet ─443─▶ caddy ─┬─▶ api ──────┐
                       └─▶ keycloak ─┼─▶ db ◀── backup (daily dumps → ./backups)
                           worker ───┘        (internal network, no route outside)
```

It is the same stack as the Compose deployment — `compose.yaml` plus the
`compose.idp.yaml` overlay — so moving to Entra ID or a KMS later is a
configuration change, not a migration to a different setup.

## Before you start

- A Linux VPS with **4 GB of memory** and 20 GB of disk (Keycloak takes about
  1 GB; building the image needs about 2 GB while it runs)
- Docker Engine with the Compose plugin **2.24 or newer**, plus `git`, `curl`
  and `openssl`
- **Ports 80 and 443 open** to the internet, in your provider's firewall too.
  Docker publishes them itself: `ufw` rules do not block them.
- Optional: your own DNS name. You need **two** A records pointing at the
  server — e.g. `desk.example.com` for Zentral and `id.desk.example.com` for
  sign-in. Without a name, setup uses `<ip>.sslip.io` and `id.<ip>.sslip.io`.

## Set up

```bash
git clone https://github.com/rogvid/zentral && cd zentral/deploy/compose
./zentralctl setup                                  # or: --hostname desk.example.com
```

It checks the server, asks for your e-mail address and name, writes `.env`
(mode 600) with generated passwords and keys, builds and starts everything,
verifies HTTPS and runs `zentral doctor`. Then it creates you as the first
administrator and prints a **one-time password**.

Open the address it shows and sign in. You will be asked to choose your own
password and to register an authenticator app (any TOTP app: Microsoft
Authenticator, Google Authenticator, 1Password, …).

### Right after setup

1. **Store the keys** in your password manager — not on the server, not next to
   the backups. Without them, neither the database nor its backups can be read:

    ```bash
    ./zentralctl keys
    ```

2. **Get the backups off the server.** `./backups` gets one dump per day; a
   backup on the same disk does not survive the disk. For example, from your
   own machine, daily:

    ```bash
    rsync -a you@server:zentral/deploy/compose/backups/ ~/zentral-backups/
    ```

3. **Add the people** who will try it, and set up Zentral itself (teams,
   systems, a first channel) with the
   [first-deployment runbook](../runbooks/first-deployment.md#in-the-application).

`zentral doctor` shows one warning on this setup — *plain-text keys from the
environment*. That is the honest state of a single server without a KMS:
whoever controls the server can read the data. Moving the keys to a
[key provider](key-custody.md) removes the warning.

## People

```bash
./zentralctl user add jane@example.com "Jane Doe" agent        # prints a one-time password
./zentralctl user add sam@example.com "Sam Berg" auditor
./zentralctl user list
./zentralctl user roles jane@example.com agent viewer          # replace roles
./zentralctl user reset jane@example.com      # lost phone / forgot password: new one-time password
./zentralctl user disable jane@example.com    # leaver: also ends their sessions
```

Roles: `admin`, `agent`, `viewer`, `auditor`, `privacy`, `automation` — see
[Roles and permissions](../compliance/rbac.md). Give the one-time password over
a different channel than the address. People change their password and
authenticator app themselves at
`https://id.<your host>/realms/zentral/account`.

Role changes and disabling take effect within five minutes (the lifetime of an
access token). Sessions end after one hour of inactivity, and after ten hours
at most.

!!! note "Segregation of duties"
    For anything beyond a trial, follow the production checklist: at least two
    administrators, and auditor and privacy officer held by *different* people
    than the administrators.

## AI agents

```bash
./zentralctl agent add triage-bot              # role: automation (default)
```

prints a client ID, a secret, the token endpoint and the MCP endpoint. The
agent obtains tokens with the client-credentials grant and calls
`https://<your host>/mcp` — see [AI agents over MCP](../guides/mcp.md).
`agent list`, `agent rotate NAME` (new secret) and `agent remove NAME` manage
them. `zentralctl` refuses to give an agent the admin role.

## Operating it

All commands run in `deploy/compose`; plain `docker compose …` works too
(`.env` selects the files and profiles).

| Task | Command |
|---|---|
| Is everything up? Newest backups? Disk? | `./zentralctl status` |
| Logs | `./zentralctl logs` (or `logs keycloak`) |
| Pre-flight check | `./zentralctl doctor` |
| Verify the whole audit trail | `./zentralctl verify` |
| Back up now | `./zentralctl backup` |
| Prove the newest backup restores and decrypts | `./zentralctl drill` — monthly |
| Restore | `./zentralctl restore backups/zentral-<time>.dump` |
| Upgrade | `./zentralctl upgrade` |

**Backups** run inside the stack (`backup` service): one dump of the Zentral
and Keycloak databases a day, deleted after `BACKUP_KEEP_DAYS` (14). Their
retention counts for GDPR: erased data survives in backups until they expire.

**Restore** asks first, takes a `pre-restore` backup of the current state,
replaces the database, and runs `doctor` and `audit verify`. A Zentral restore
undoes erasures made after the backup was taken: repeat them
([Backup and restore](../runbooks/backup-restore.md#restore), step 6).
Keycloak dumps restore the same way (`restore backups/keycloak-<time>.dump`).

**Upgrade** checks health, takes a `pre-upgrade` backup, pulls the code
(`git pull --ff-only`), rebuilds, refreshes the PostgreSQL, Caddy and Keycloak
images to their latest patch release, restarts (migrations run first) and
checks again. If the new version is unhealthy, restore the `pre-upgrade` dump
and check out the previous commit ([Upgrade](../runbooks/upgrade.md)).

**After a reboot** everything starts by itself (`restart: unless-stopped`).

**Logs** are rotated (5 × 10 MB per container). Keycloak keeps sign-in events
for 90 days.

**Host:** keep the server patched (`unattended-upgrades`), allow SSH by key
only, and watch disk space.

## Security notes

- Production mode: Zentral refuses unsafe configuration at start-up, sends
  HSTS, and accepts only tokens from the bundled realm, for its own audience.
- Sign-in requires password + authenticator app for every person; after ten
  failed attempts an account is locked temporarily (up to 15 minutes). No self-registration, no
  password grant, no implicit flow; the web client must use PKCE.
- Published to the internet: Zentral, and the realm's sign-in, token and
  account endpoints. **Not** published: Keycloak's admin console, the master
  realm, health and metrics endpoints, the database.
- Containers run unprivileged with all capabilities dropped; the database and
  Keycloak have no outbound network access.
- Secrets live in `.env` (mode 600) on the server. Whoever has root on the
  server can read all data: protect the server accordingly, or add a
  [key provider](key-custody.md).

## Troubleshooting

| Symptom | Cause |
|---|---|
| Setup warns "No valid HTTPS answer yet", browser shows a certificate error | Ports 80/443 not reachable from the internet (provider firewall), or DNS not pointing at the server. Fix it; Caddy retries by itself (`./zentralctl logs caddy`). |
| "Identity provider metadata does not match" in the browser | `ZENTRAL_IDP_HOSTNAME` was changed after setup; see *Changing the hostname* below. |
| Sign-out shows "Do you want to log out?" | Expected: confirm once. |
| Signed in, but "Your role does not include …" | `./zentralctl user list` — give the person a role. |
| Someone is locked out | Wait 15 minutes, or `./zentralctl user reset EMAIL`. |
| `zentralctl user …`: "Keycloak is not healthy" | `./zentralctl logs keycloak`; it needs about a minute after a start. |
| Anything else | [Troubleshooting runbook](../runbooks/troubleshooting.md) |

### Changing the hostname

Edit `ZENTRAL_HOSTNAME`, `ZENTRAL_IDP_HOSTNAME`, `ZENTRAL_PUBLIC_BASE_URL` and
`ZENTRAL_ALLOWED_HOSTS` in `.env`. The realm was imported with the old address,
so also update the `zentral-web` client's redirect URI, web origin and
post-logout URI with Keycloak's admin CLI (`docker compose exec keycloak
/opt/keycloak/bin/kcadm.sh …`), then `docker compose up -d`.

## Moving to your own identity provider or KMS later

- **Entra ID / another OIDC provider:** remove `compose.idp.yaml` from
  `COMPOSE_FILE` in `.env`, add the identity block from `.env.example`, and
  `docker compose up -d --remove-orphans`. People get new subjects, so re-add
  team memberships ([Changing provider](identity-oidc.md#changing-provider-later)).
- **KMS:** follow [Move an existing installation](key-custody.md#move-an-existing-installation).
