# Changelog

All notable changes to Zentral. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The release workflow publishes the
section for a version as its release notes.

## [Unreleased]

### Added

- **Error monitoring with Sentry**, off unless configured
  (`ZENTRAL_SENTRY_DSN`, build argument `VITE_SENTRY_DSN`). Only code-level data
  is sent: error types, stack frames without variables, endpoint names, status
  codes, timings and the release.
- `ZENTRAL_DEV_AUTH_PASSWORD`: development sign-in on a test server in
  production mode, behind a password of at least 32 characters. Without it
  production still refuses development sign-in.
- Deploy workflow for the maintainers' own server (`.github/workflows/deploy.yml`).
- **Deploy on a single VPS** without Entra ID, another identity provider or a
  KMS: `deploy/compose/zentralctl setup` runs the Compose stack in production
  mode with a bundled Keycloak (`compose.idp.yaml`): password plus required
  authenticator app, lock-out, no public admin console. `zentralctl` also
  runs backups, restores, restore drills and upgrades, and resumes an
  interrupted setup. Without a DNS name, certificates are issued for
  `<ip>.sslip.io`.
- `zentral idp user …` / `zentral idp agent …`: people and AI agents in the
  bundled realm, through Keycloak's admin API, with roles taken from
  Zentral's own role list. Settings `ZENTRAL_KEYCLOAK_URL`, `_REALM`,
  `_ADMIN_USER`, `_ADMIN_PASSWORD`. The realm now also defines `Zentral.Ingest`.
- Compose: opt-in `backup` profile with daily database dumps and a freshness
  health check; container log rotation; Caddy pinned to 2.11; `Caddyfile`
  imports extra sites from `sites/*.caddy`.

### Fixed

- Tracebacks on stdout are now scrubbed like every other log value. The text
  of an unhandled exception could carry e-mail addresses, passwords and other
  identifiers from the request into the container's log. Secrets are also
  recognised when quoted, as in `{'password': 'two words'}`. Names and other
  free text no pattern recognises can still appear in an exception's message.

## [1.0.0]

The release intended for production. Read **Upgrading** first.

### Upgrading

- **Production refuses plain-text keys in the environment.** Before upgrading
  a production installation, move its keys into a keyring
  (`zentral keys wrap`, see *Key custody* in the documentation) or set
  `ZENTRAL_ALLOW_ENV_KEYS=true` to keep the previous behaviour knowingly;
  `zentral doctor` then warns.
- The Kubernetes base now runs API and worker under the `zentral` service
  account with Azure workload identity, and mounts the keyring from the
  ConfigMap `zentral-keyring`. Keys are no longer in `zentral-secrets`.
- The image is built on Python 3.15 and Node 26.

### Added

- **Key custody with a KMS or HSM** (#8). Keys live in an envelope-encrypted
  keyring whose data key is wrapped by Azure Key Vault / Managed HSM or
  HashiCorp Vault Transit, unwrapped once at start-up. Plug-in interface for
  other key stores. New commands: `zentral keys init`, `wrap`, `rewrap`,
  `add-data-key`, `retire-data-keys`, `rotate-audit-key`, `show`,
  `new-local-kek` and `verify-escrow`. `zentral doctor` reports key custody.
- **Restore drill** (`scripts/restore_drill.py`): backs up, restores into a
  scratch database, runs `doctor`, `audit verify` and the escrow check, and
  prints a timed report (#20).
- **Load-test tool** (`scripts/loadtest.py`) and a sizing guide with measured
  results on PostgreSQL (#20).
- `zentral serve --workers N` runs several server processes on one host.
- **Release workflow**: image published to GitHub Container Registry with an
  SBOM and signed build provenance; GitHub release with these notes and the
  Python package.
- Documentation: key custody, sizing, release and staging runbook, a
  pre-filled DPIA and a penetration-test brief (#9).

### Changed

- Dependencies: Python 3.15 and Node 26 base images; GitHub Actions
  checkout/setup-node v7 and setup-uv v7; Vite 8, Vitest 5, TypeScript 7,
  Mermaid 12 in the web client (#20).
- `zentral init` for production writes key-provider settings instead of
  plain-text keys.

### Fixed

- Two messages arriving at the same moment from a requester Zentral had not
  seen before could fail one of them with a server error (a race when creating
  the contact). Found by the load test (#20).
- Insights and search no longer decrypt content they do not use; one server
  process handles about a third more requests (#20).

### Security

- The documented residual risk "keys in environment variables" is closed for
  installations using a key provider; the threat model and security posture
  are updated accordingly.

## [0.1.0]

First complete release (#21): intake from Microsoft 365 mail, Teams, webhooks,
the requester portal and AI agents (MCP); AI triage on masked text with
on-prem models; teams, systems, rules, service levels on working hours,
escalation and notifications; replies, saved replies, merging, attachments
with malware scanning, ratings and Insights; Entra ID or OIDC sign-in with
role- and team-based access; field encryption, hash-chained audit trail with
signed archives, GDPR access, erasure, retention and objection to AI.

[1.0.0]: https://github.com/rogvid/zentral/compare/2bfc2ef...v1.0.0
[0.1.0]: https://github.com/rogvid/zentral/commit/2bfc2ef
