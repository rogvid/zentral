# Configuration reference

All configuration is environment variables prefixed `ZENTRAL_` (a `.env` file
in the working directory is read too). Generate a starting point with
`zentral init`, and check the result with `zentral doctor`.

**List values** are comma-separated (`a,b,c`); a JSON array also works.
**Mappings** (`ZENTRAL_ROLE_MAP`) are `key=value,key=value` or a JSON object.

## The settings you will actually touch

| Group | Variables |
|---|---|
| **Basics** | `ZENTRAL_ENV` (`production`), `ZENTRAL_PUBLIC_BASE_URL`, `ZENTRAL_ALLOWED_HOSTS`, `ZENTRAL_DATABASE_URL` |
| **Identity** | `ZENTRAL_AUTH_MODE` + [`ZENTRAL_ENTRA_*`](identity-entra.md) or [`ZENTRAL_OIDC_*`](identity-oidc.md), `ZENTRAL_ROLE_MAP` |
| **Keys** | `ZENTRAL_ENCRYPTION_KEYS`, `ZENTRAL_BLIND_INDEX_KEY`, `ZENTRAL_AUDIT_HMAC_KEY` |
| **AI** | `ZENTRAL_AI_PROVIDER`, `ZENTRAL_AI_BASE_URL`, `ZENTRAL_AI_MODEL`, `ZENTRAL_AI_MIN_CONFIDENCE` |
| **Data protection** | `ZENTRAL_RETENTION_DAYS`, `ZENTRAL_AI_REDACT_PII` |
| **Process** | `ZENTRAL_CATEGORIES`, `ZENTRAL_REQUIRE_SYSTEM_TO_RESOLVE`, `ZENTRAL_TICKET_PREFIX`, `ZENTRAL_SLA_RESPONSE_HOURS`, `ZENTRAL_SLA_RESOLVE_HOURS`, `ZENTRAL_SLA_CALENDAR`, `ZENTRAL_BUSINESS_HOURS`, `ZENTRAL_TIMEZONE`, `ZENTRAL_HOLIDAYS`, `ZENTRAL_ESCALATION_WARN_FRACTION`, `ZENTRAL_AUTO_CLOSE_DAYS` |
| **Plug-ins** | `ZENTRAL_ENABLED_CONNECTORS`, `ZENTRAL_OUTBOUND_HOST_ALLOWLIST` |

## Notes on individual settings

`ZENTRAL_DATABASE_URL`
:   `postgresql+asyncpg://user:password@host:5432/dbname?ssl=require`. SQLite
    (`sqlite+aiosqlite:///./file.db`) is for development only and refused in
    production.

`ZENTRAL_ENCRYPTION_KEYS`
:   One or more Fernet keys, newest first. The first encrypts; all decrypt.
    See [key rotation](../runbooks/key-rotation.md). **Losing these keys means
    losing the data.**

`ZENTRAL_CATEGORIES`
:   The ticket categories. Changing the list does not rewrite existing
    tickets. Keep `other` in the list: it is where unrecognised AI answers land.

`ZENTRAL_RETENTION_DAYS`
:   Days after resolution before a ticket's content is scrubbed. Set it to the
    period your retention policy justifies.

`ZENTRAL_SLA_RESPONSE_HOURS`, `ZENTRAL_SLA_RESOLVE_HOURS`
:   Targets per priority, in hours, e.g.
    `urgent=1,high=4,normal=8,low=24`. A priority left out has no target. A
    [system](../guides/systems.md#targets-per-system) can replace them for its
    own tickets. A change applies to tickets created, re-prioritised or moved
    to another system after it.

`ZENTRAL_BUSINESS_HOURS`, `ZENTRAL_TIMEZONE`, `ZENTRAL_HOLIDAYS`
:   When the business clock runs, e.g. `mon-fri 08:00-17:00` (several windows:
    `mon-fri 08:00-12:00, mon-fri 13:00-17:00, sat 09:00-13:00`), in an IANA
    time zone such as `Europe/Copenhagen`, minus holidays given as
    `2026-12-24,2026-12-25`. Daylight-saving changes are handled. Empty
    working hours (the default) means every priority runs around the clock.

`ZENTRAL_SLA_CALENDAR`
:   Which clock each priority uses: `business` or `24x7`. Default:
    `urgent=24x7,high=business,normal=business,low=business` — an urgent
    ticket on Friday evening is due Friday evening; a normal one waits for
    Monday morning.

`ZENTRAL_ESCALATION_WARN_FRACTION`
:   When a target counts as "due soon": the share of its time that is left.
    `0.25` warns in the last quarter; `0` turns warnings off (missed targets
    still escalate to the team).

`ZENTRAL_AUTO_CLOSE_DAYS`
:   Days a resolved ticket waits before it is closed; `0` turns it off.
    Retention still counts from resolution.

`ZENTRAL_AI_REDACT_PII`
:   Mask personal identifiers before text is sent to a model. Leave on. Cannot
    be disabled for cloud providers in production.

`ZENTRAL_ENABLED_CONNECTORS`
:   Allow-list of connector plug-ins that may be loaded. A plug-in that is
    installed but not listed is never imported.

`ZENTRAL_OUTBOUND_HOST_ALLOWLIST`
:   Hosts connectors may call (suffix match on a dot boundary). Add hosts for
    third-party connectors.

`ZENTRAL_STATIC_DIR`
:   Directory with the built web client. Set in the container image
    (`/app/web`). Without it, only the API and MCP are served.

`ZENTRAL_MAX_BODY_BYTES`, `ZENTRAL_RATE_LIMIT_PER_MINUTE`, `ZENTRAL_HOOK_RATE_LIMIT_PER_MINUTE`
:   Abuse limits. The rate limits are per process; mirror them in your proxy.

Besides these, the `anthropic` AI provider reads its credentials the standard
way for the Anthropic SDK (`ANTHROPIC_API_KEY`) unless `ZENTRAL_AI_API_KEY` is
set.

## Start-up safety checks

With `ZENTRAL_ENV=production` the process refuses to start if the
configuration would weaken security:
development sign-in · SQLite · a non-HTTPS public URL or OIDC issuer · wildcard
CORS · a missing or short key · PII masking disabled for a cloud AI provider ·
the `claude_cli` test provider.

## Command line

| Command | Purpose |
|---|---|
| `zentral init [--auth entra\|oidc\|dev] [--output .env]` | Write a starter configuration with fresh keys |
| `zentral doctor` | Pre-flight checks; exit 1 on failure |
| `zentral serve [--host] [--port] [--proxy-headers --forwarded-allow-ips …]` | Run the server |
| `zentral worker` | Run the background worker |
| `zentral db upgrade` / `zentral db current` | Apply / show schema migrations |
| `zentral keys generate` | Print a new set of keys |
| `zentral keys rotate` | Re-encrypt all data under the first encryption key |
| `zentral audit verify` | Verify the audit chain; exit 1 if tampered |
| `zentral audit export FILE [--after-seq N]` | Export audit events as JSON Lines |

## All settings

Generated from the code (`scripts/gen_docs.py`); CI fails if it is out of date.

<!-- BEGIN GENERATED: config -->
| Variable | Default | Type |
|---|---|---|
| `ZENTRAL_ENV` | `development` | one of ['development', 'test', 'production'] |
| `ZENTRAL_DATABASE_URL` | `sqlite+aiosqlite:///./zentral-dev.db` | str |
| `ZENTRAL_PUBLIC_BASE_URL` | `http://localhost:8000` | str |
| `ZENTRAL_ALLOWED_HOSTS` | `localhost,127.0.0.1` | list[str] |
| `ZENTRAL_CORS_ORIGINS` | *(empty)* | list[str] |
| `ZENTRAL_STATIC_DIR` | *(none)* | str, optional |
| `ZENTRAL_TICKET_PREFIX` | `ZEN` | str |
| `ZENTRAL_REQUIRE_SYSTEM_TO_RESOLVE` | `False` | bool |
| `ZENTRAL_SLA_RESPONSE_HOURS` | `{'urgent': 1.0, 'high': 4.0, 'normal': 8.0, 'low': 24.0}` | dict[str, float] |
| `ZENTRAL_SLA_RESOLVE_HOURS` | `{'urgent': 8.0, 'high': 24.0, 'normal': 72.0, 'low': 168.0}` | dict[str, float] |
| `ZENTRAL_BUSINESS_HOURS` | *(empty)* | str |
| `ZENTRAL_TIMEZONE` | `UTC` | str |
| `ZENTRAL_HOLIDAYS` | *(empty)* | list[str] |
| `ZENTRAL_SLA_CALENDAR` | `{'urgent': '24x7', 'high': 'business', 'normal': 'business', 'low': 'business'}` | dict[str, str] |
| `ZENTRAL_ESCALATION_WARN_FRACTION` | `0.25` | float |
| `ZENTRAL_AUTO_CLOSE_DAYS` | `7` | int |
| `ZENTRAL_AUTH_MODE` | `entra` | one of ['entra', 'oidc', 'dev'] |
| `ZENTRAL_ROLE_MAP` | *(empty)* | dict[str, str] |
| `ZENTRAL_ENTRA_TENANT_ID` | *(empty)* | str |
| `ZENTRAL_ENTRA_API_CLIENT_ID` | *(empty)* | str |
| `ZENTRAL_ENTRA_SPA_CLIENT_ID` | *(empty)* | str |
| `ZENTRAL_ENTRA_AUTHORITY_HOST` | `https://login.microsoftonline.com` | str |
| `ZENTRAL_ENTRA_REQUIRED_SCOPE` | `access_as_user` | str |
| `ZENTRAL_ENTRA_EXTRA_AUDIENCES` | *(empty)* | list[str] |
| `ZENTRAL_OIDC_ISSUER` | *(empty)* | str |
| `ZENTRAL_OIDC_JWKS_URI` | *(empty)* | str |
| `ZENTRAL_OIDC_AUDIENCES` | *(empty)* | list[str] |
| `ZENTRAL_OIDC_ALGORITHMS` | `RS256` | list[str] |
| `ZENTRAL_OIDC_CLIENT_ID` | *(empty)* | str |
| `ZENTRAL_OIDC_SCOPES` | `openid,profile` | list[str] |
| `ZENTRAL_OIDC_REQUIRED_SCOPE` | *(empty)* | str |
| `ZENTRAL_OIDC_SUBJECT_CLAIM` | `sub` | str |
| `ZENTRAL_OIDC_SCOPE_CLAIM` | `scope` | str |
| `ZENTRAL_OIDC_ROLES_CLAIM` | `roles` | str |
| `ZENTRAL_OIDC_GROUPS_CLAIM` | `groups` | str |
| `ZENTRAL_OIDC_NAME_CLAIM` | `name` | str |
| `ZENTRAL_OIDC_EMAIL_CLAIM` | `email` | str |
| `ZENTRAL_CSP_CONNECT_EXTRA` | *(empty)* | list[str] |
| `ZENTRAL_ENCRYPTION_KEYS` | *(secret, none)* | list[SecretStr] |
| `ZENTRAL_BLIND_INDEX_KEY` | *(secret, none)* | SecretStr |
| `ZENTRAL_AUDIT_HMAC_KEY` | *(secret, none)* | SecretStr |
| `ZENTRAL_AUDIT_HMAC_PREVIOUS_KEYS` | *(secret, none)* | list[SecretStr] |
| `ZENTRAL_RETENTION_DAYS` | `365` | int |
| `ZENTRAL_AI_PROVIDER` | `rules` | str |
| `ZENTRAL_AI_MODEL` | *(empty)* | str |
| `ZENTRAL_AI_BASE_URL` | *(empty)* | str |
| `ZENTRAL_AI_API_KEY` | *(secret, none)* | SecretStr |
| `ZENTRAL_AI_TIMEOUT_SECONDS` | `30.0` | float |
| `ZENTRAL_AI_REDACT_PII` | `True` | bool |
| `ZENTRAL_AI_MIN_CONFIDENCE` | `0.6` | float |
| `ZENTRAL_CATEGORIES` | `incident,service_request,access,hardware,software,network,security,billing,other` | list[str] |
| `ZENTRAL_ENABLED_CONNECTORS` | `webhook,teams,email_graph` | list[str] |
| `ZENTRAL_OUTBOUND_HOST_ALLOWLIST` | `graph.microsoft.com,login.microsoftonline.com,webhook.office.com,logic.azure.com,api.powerplatform.com` | list[str] |
| `ZENTRAL_POLL_INTERVAL_SECONDS` | `60` | int |
| `ZENTRAL_MAX_BODY_BYTES` | `1048576` | int |
| `ZENTRAL_MAX_MESSAGE_CHARS` | `100000` | int |
| `ZENTRAL_RATE_LIMIT_PER_MINUTE` | `300` | int |
| `ZENTRAL_HOOK_RATE_LIMIT_PER_MINUTE` | `120` | int |
| `ZENTRAL_AUTH_FAILURE_LIMIT_PER_MINUTE` | `30` | int |
| `ZENTRAL_LOG_LEVEL` | `INFO` | str |
<!-- END GENERATED -->
