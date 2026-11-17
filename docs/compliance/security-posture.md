# Security posture

An honest statement of what has been verified, what has not, and what you
still need to do before trusting Zentral with personal data. Read this before
go-live, and give it to whoever accepts the risk.

!!! warning "No certification, no independent test"
    Zentral has **not** been penetration-tested or audited by an independent
    party, and holds no certification (ISO 27001, SOC 2, or other). The
    controls described in this documentation are implemented and covered by
    automated tests, which is evidence of *intent and behaviour*, not a
    guarantee of the absence of vulnerabilities.

## Verified by automated tests

Each line corresponds to tests that run in CI on every change, on both SQLite
and PostgreSQL.

| Property | How it is tested |
|---|---|
| Invalid tokens are rejected | Expired, wrong audience/issuer/tenant, missing scope or subject, wrong key, unknown key, `alg=none`, HMAC key-confusion, malformed |
| Every API route requires authentication | The OpenAPI schema is walked; any route outside a three-entry allow-list must answer 401 without a token |
| Roles grant exactly what the matrix says | Permission-set assertions; a forbidden-matrix of role × endpoint; unknown roles grant nothing |
| Team scope holds for every operation | Out-of-scope read/update/note → 404 identical to "missing", and audited |
| Personal data is masked without the permission | API and MCP responses searched for the raw values |
| Nothing personal is stored in clear | Every table dumped and searched for names, addresses, bodies, secrets, tokens |
| Erasure leaves nothing decryptable | Every remaining ciphertext is decrypted and searched |
| Audit trail detects tampering | Modify, delete, forge with a different key → verification fails at the right event |
| Audit trail is append-only | `UPDATE`/`DELETE`/`TRUNCATE` rejected by PostgreSQL |
| Action and audit commit atomically | Audit write forced to fail → the action is not persisted |
| Audit and logs carry no content | Trail dumped and searched |
| Webhook authentication | Missing/wrong/cross-channel token, disabled or unknown channel → identical 401; Teams and Slack signatures, tampering, replay |
| Ticket threading cannot be hijacked | Guessing a ticket key as another sender creates a new ticket |
| Untrusted text is neutralised | HTML/script stripped; control characters removed |
| Mail access is scoped | Tenant-wide permission, readable probe, inconclusive probe, unreadable token → **zero** mailbox reads; later widening stops the channel; sending is guarded too |
| Mail filtering and loop protection | Allow/block lists, look-alike domains, automatic replies, own address; mailbox untouched by default |
| Repeated bad tokens are throttled | Per-address limit, successful requests unaffected |
| Text masking runs in linear time | 200 kB of pattern-bait must finish quickly (a quadratic pattern was found and fixed this way) |
| Outbound calls are allow-listed | Metadata address, localhost, look-alike hosts, plain HTTP, embedded credentials → refused |
| Key custody | Keyring tamper detection (header, nonce, ciphertext); foreign provider or KEK refused; a keyring cannot redirect Key Vault calls to another host, key or path; production refuses plain-text and duplicate environment keys and the `local_file` provider; retiring a data key refused while any value still needs it; escrow copy checked against the keys in use |
| AI output cannot escape the allow-list | Unknown category/system, out-of-range confidence, multi-line summary |
| AI cannot cause actions | Injection text + hostile model answer → no outbound request, no job, no state change beyond labels |
| AI failure does not stop intake | Provider error → rules fallback, recorded |
| Unsafe production configuration is refused | Each unsafe setting individually |
| Hardening headers, host check, size limit, rate limit | Direct assertions |
| Development sign-in is absent unless enabled | Endpoints return 404 in a production-style app |
| PKCE and redirect rules of the dev IdP | Wrong verifier, reused code, foreign redirect, `plain` method → refused |
| Migrations match the models | Schema diff must be empty |
| Key rotation works | Rotate, drop the old key, read the data |

Additionally: static analysis (`ruff` including security rules, `bandit`,
strict `mypy`), dependency audits (`pip-audit`, `npm audit`), and a browser
end-to-end test (`scripts/e2e.sh`) that signs in as every persona through the
real sign-in flow and fails on any console error, Content-Security-Policy
violation or unexpected HTTP error. It also checks that hostile ticket text is
displayed and never executed, that stored secrets never reach the page, and
that drafts never reach browser storage.

## Verified manually during development

- Full test suite against **PostgreSQL 16**.
- **Load test** on PostgreSQL 16: no errors at up to 200 concurrent virtual
  users; 68,600 audit events written concurrently by three processes, all
  verified. Found and fixed a race on first contact
  ([sizing](../operations/sizing.md)).
- **Restore drill** of a 300 MiB database: backup, restore, decryption, audit
  verification and the escrow key check in 22 seconds.
- Live classification with **Claude Haiku 5.5** through the `claude_cli`
  provider (subscription sign-in).
- Web client end-to-end in Chromium through the real authorization-code + PKCE
  flow against the development identity provider, in light and dark themes.
- The built wheel installed outside the source tree: `init`, `db upgrade`,
  `doctor`.

## Not verified — treat as untested until you have checked

| Area | Status |
|---|---|
| **Sign-in against a real Entra ID tenant** | Implemented to Microsoft's documented token format and flow; **never run against a live tenant** |
| **Other OIDC providers** (Keycloak, Okta, Auth0, AD FS) | Validated against tokens shaped like each provider's; recipes not tested on live instances |
| **Microsoft Graph mail** | Tested against a stubbed Graph API only — including the scope check, whose assumptions about Exchange's responses (403 for an out-of-scope mailbox; no `Mail.*` role in the token under RBAC for Applications) follow Microsoft's documentation but have not been observed on a live tenant |
| **Microsoft Teams** | Signature scheme and payload tested with constructed requests only |
| **Anthropic API provider** | Tested against a stubbed API (the CLI provider was tested live) |
| **On-prem model servers** | Protocol tested against a stub; not against a running Ollama/vLLM |
| **Container image** | Dockerfile not built in the development environment (no Docker daemon); CI builds and smoke-tests it |
| **Compose stack** | Validated with `docker compose config`; not started |
| **Kubernetes manifests** | Validated for syntax and hardening fields; not applied to a cluster |
| **Load and concurrency** | Load-tested on PostgreSQL with up to 200 virtual users over three server processes on one 4-vCPU host ([sizing](../operations/sizing.md)); not yet on production hardware or across hosts |
| **Accessibility** | Semantic HTML and keyboard operation by design; no formal audit |

## Known limitations

1. **Keys in process memory.** With a [key provider](../operations/key-custody.md)
   the keys are kept out of configuration and storage and unwrapped by a KMS
   at start-up, but the running process holds them: a full compromise of the
   application host while it runs exposes the data.
2. **Masking is pattern-based.** Names and context in free text are not
   detected. It is data minimisation, not anonymisation.
3. **Metadata is not encrypted**: statuses, timestamps, tags, team/system
   links, staff names, audit actor IDs and IP addresses.
4. **Attachment scanning is signature-based** (ClamAV); a novel malicious
   PDF can pass. PDFs are downloads only, never rendered by Zentral.
5. **Search** covers only the 1,000 most recent visible tickets.
6. **Rate limiting is per process** and in memory.
7. **Audit**: tail truncation needs an external copy to detect; list views are
   audited as a count; no retention tooling; a leaked *retired* audit key still
   verifies.
8. **Blind-index key cannot be rotated** without a data migration.
9. **Tokens in the browser** live in session storage, protected by the strict
   Content-Security-Policy; a backend-for-frontend pattern would remove them
   from script reach entirely.
10. **Access-token lifetime** bounds how quickly a removed role takes effect
    (typically up to an hour); there is no revocation list.
11. **E-mail sender authenticity** depends on your mail system's anti-spoofing.
12. **Single database** is the single point of failure and of truth.
13. **No licence has been chosen** for this code base yet; decide before
    distributing it.

## Before go-live: your checklist

- [ ] **Penetration test** by an independent party ([brief](pentest-brief.md))
- [ ] **DPIA** ([pre-filled draft](dpia.md)) and entry in the record of
      processing activities
- [ ] **Identity integration tested** in your tenant: sign-in, each role, a
      service identity, role removal taking effect
- [ ] **Each channel tested** end to end with real (non-sensitive) messages
- [ ] Mail connector **scoped to the one mailbox** and verified
- [ ] Keys in a [KMS-wrapped keyring](../operations/key-custody.md), escrow
      copy in a safe; **restore drill passed** with them
      (`scripts/restore_drill.py`)
- [ ] TLS configuration reviewed (e.g. testssl.sh / SSL Labs)
- [ ] WAF / rate limiting in front of the public endpoints
- [ ] Logs to SIEM; alerts from [Monitoring](../operations/monitoring.md) live
- [ ] Daily audit verification scheduled; export to immutable storage
- [ ] AI provider is `rules` or on-prem; verified in the audit trail
- [ ] Admin / Auditor / PrivacyOfficer are **different people**
- [ ] Runbooks rehearsed: restore, key rotation, incident
- [ ] The limitations above reviewed and **accepted in writing** by the risk
      owner

## Reporting a vulnerability

See `SECURITY.md` in the repository. Please report privately; do not open a
public issue.
