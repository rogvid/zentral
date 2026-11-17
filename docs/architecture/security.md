# Security architecture

This page describes the controls. The [threat model](threat-model.md) explains
which threats each one answers, and [Security posture](../compliance/security-posture.md)
states what has and has not been independently verified.

## Identity and authentication

Zentral is an OAuth 2.0 **resource server**. It holds no passwords, issues no
long-lived credentials to people, and keeps no server-side sessions.

- Every request carries an access token from the installation's identity
  provider — [Microsoft Entra ID](../operations/identity-entra.md) or
  [another OIDC provider](../operations/identity-oidc.md).
- Validation is strict and identical for every client: signature against the
  provider's published keys; algorithm from an **asymmetric-only allow-list**
  (never `none`, never HMAC); exact issuer; audience; expiry; tenant (Entra);
  required scope for people; at least one role for services.
- Signing keys are cached and refreshed on rollover, at most once a minute —
  forged tokens with random key IDs cannot be used to make Zentral hammer the
  identity provider. If keys cannot be fetched, requests are **rejected**.
- Authentication failures return one generic `401` with no detail, and an
  address that keeps presenting bad tokens is throttled before any further
  cryptographic work is done for it.
- The browser signs in with the authorization-code flow and PKCE. There are no
  cookies, hence no CSRF surface.

**Machine callers**

| Caller | Credential | Can do |
|---|---|---|
| A service raising issues | Per-channel ingest token (`zis_…`); only its SHA-256 is stored | Create messages on that one channel |
| Teams / Slack | HMAC signature with a per-channel secret | Create messages on that one channel |
| An AI agent or script | Access token from the identity provider | Whatever its roles allow |

## Authorisation

Three layers, all enforced in the service layer (never in the UI alone):

1. **Permissions** from roles — see the [generated matrix](../compliance/rbac.md).
   Roles are assigned in the identity provider; Zentral stores no role
   assignments.
2. **Team scope** — a ticket is visible only if it belongs to one of your
   teams, is assigned to you, or is untriaged and you hold `ticket:triage`.
   Tickets outside your scope answer exactly like tickets that do not exist
   (`404`), and the attempt is audited.
3. **Personal-data masking** — without `ticket:read_pii`, requester identity
   is hidden and free text is passed through the redactor.

## Data protection

| At rest | In transit | In use |
|---|---|---|
| Field-level encryption of all free text and requester data; blind indexes for lookups ([data model](data-model.md)). Use disk/volume encryption underneath as well. | TLS terminated at your proxy or ingress; HSTS in production. Database connections should use TLS (`?ssl=require`). | Decrypted only in application memory for an authorised request. Never written to logs. |

Three keys, each with one job:

| Key | Purpose | If lost | If leaked |
|---|---|---|---|
| `ZENTRAL_ENCRYPTION_KEYS` | Encrypt fields | Encrypted data is unrecoverable | Rotate ([runbook](../runbooks/key-rotation.md)); a DB dump becomes readable to the holder |
| `ZENTRAL_BLIND_INDEX_KEY` | Lookup hashes | Lookups by e-mail and de-duplication stop matching old data | Holder can test guesses against a DB dump |
| `ZENTRAL_AUDIT_HMAC_KEY` | Audit chain | Existing chain can no longer be verified | Holder with DB write access could forge a consistent chain |

Store them in a secret manager, never in the image or repository, and back
them up **separately from the database**.

## Audit trail

Every state change, every read of ticket content, every privacy operation and
every denied attempt is recorded — see [Logging and audit](../compliance/logging.md).
Three properties matter:

- **Atomic**: the event commits in the same transaction as the action.
- **Tamper-evident**: events form an HMAC-SHA256 hash chain; any edit, deletion
  or reordering is detected by `zentral audit verify`.
- **Append-only at the database**: on PostgreSQL, triggers reject `UPDATE`,
  `DELETE` and `TRUNCATE` on the audit table.

## Untrusted content

Everything that arrives through a channel is hostile until proven otherwise.

- **Plain text only.** HTML is reduced to text on ingest; control and
  bidirectional-override characters are stripped. The web client renders text
  nodes only and never injects HTML.
- **Strict Content-Security-Policy**: no inline script or style, no framing,
  network access limited to this origin and the identity provider.
- **Threading cannot be hijacked.** A ticket key in a subject line only threads
  onto that ticket if the sender is its requester.
- **Prompt injection is contained by design** — see [AI pipeline](ai.md).
- **Outbound calls are allow-listed** (`ZENTRAL_OUTBOUND_HOST_ALLOWLIST`),
  HTTPS only, redirects disabled: a connector cannot be pointed at internal
  addresses.

## Scoped integrations

Credentials Zentral holds for other systems are limited to what the channel
needs, and where the platform allows it Zentral **verifies** the limit instead
of assuming it. The Microsoft 365 mail connector proves, before reading any
mail and again every hour, that its credentials cannot reach other mailboxes;
if it cannot prove it, it does not run
([details](../guides/connectors/email.md#how-the-scope-check-works)).

## Abuse resistance

Body-size limit (1 MiB by default) enforced before parsing; bounded string
lengths and `extra="forbid"` on every input model; pagination caps; per-principal
and per-channel rate limits; a per-address limit on failed sign-ins; pattern
matching on untrusted text is bounded to linear time; host-header allow-list; DNS-rebinding protection
on the MCP endpoint. Validation errors never echo submitted values.

## Runtime hardening

The container runs as an unprivileged user with a read-only root filesystem,
no Linux capabilities and no service-account token. The provided Kubernetes
manifests meet the *restricted* Pod Security Standard and ship default-deny
network policies; the Compose stack keeps the database on a network with no
route to the outside.

## Supply chain

Few dependencies, all pinned with hashes in lock files (`uv.lock`,
`package-lock.json`). CI runs `pip-audit`, `npm audit` and `bandit`; Dependabot
proposes updates weekly. Connector and AI plug-ins are only loaded if named in
an explicit allow-list, because loading a plug-in executes its code.

## Configuration that cannot be weakened by accident

In production the process refuses to start if: development sign-in is enabled;
the database is SQLite; the public URL is not HTTPS; CORS allows `*`; any of
the three keys is missing or short; PII masking is disabled for a cloud AI
provider; or the test-only `claude_cli` provider is selected.
