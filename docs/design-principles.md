# Design principles

These are the rules the code base is held to. When a change conflicts with one
of them, the change is wrong or the principle needs an explicit
[decision record](architecture/decisions.md).

## 1. Privacy by design, not by policy

Protection is built into the data path rather than promised in a document.

- **Encrypt what is personal.** Message bodies, subjects, requester names and
  addresses, AI summaries and connector secrets are encrypted *by the
  application* before they reach the database. A database dump, backup or
  replica alone discloses nothing readable.
- **Minimise what leaves.** Text is masked for personal identifiers before it
  is sent to any AI model, and for any reader whose role lacks
  `ticket:read_pii`.
- **Forget on schedule.** Resolved tickets are scrubbed after the retention
  period; people with no remaining tickets are erased automatically.
- **Logs are not a shadow database.** Operational logs never contain bodies,
  subjects or query strings; the audit trail stores identifiers and
  enumerations, never content.

## 2. Least privilege everywhere

- **Deny by default.** A principal has exactly the permissions of its roles;
  unknown roles grant nothing; every API route requires authentication unless
  it is on a short, tested allow-list.
- **Scope by team.** Having a role is not enough to see a ticket — it must
  also belong to one of your teams (or be untriaged, if you may triage).
- **Separate duties.** Administrators configure but cannot export or erase
  personal data; privacy officers can, but cannot reconfigure; auditors see the
  trail but no ticket content.
- **Narrow machine identities.** A webhook token can create tickets on one
  channel and nothing else. The recommended role for AI agents cannot see
  personal data and cannot message anyone outside.
- **Narrow infrastructure.** Containers run as non-root with a read-only
  filesystem and no capabilities; the database is unreachable from outside;
  outbound calls are allow-listed.

## 3. One door for everyone

People, scripts and AI agents use the same identity provider, pass through the
same authorisation code and leave the same audit trail. The REST API, the MCP
server and the background worker all call one service layer; there is no
"internal" path that skips a check.

## 4. AI advises, rules decide

A language model may *suggest* a category, priority, system and summary. Its
output is forced back into an allow-list, and what happens next is determined
by deterministic rules that an administrator wrote. A model has no tools, so
whatever a malicious e-mail says, the worst outcome is a wrong label. See
[AI pipeline](architecture/ai.md).

## 5. Fail closed

- Unsafe production configuration stops the process at start-up.
- If signing keys cannot be fetched, tokens are rejected, not waved through.
- If the audit record cannot be written, the action itself is rolled back.
- If the AI provider fails, a local rules classifier takes over; intake never
  depends on a model being available.

## 6. Small, replaceable parts

Channels, AI providers and identity providers are plug-ins behind narrow
contracts. Plug-ins never touch the database; the security-critical logic
(de-duplication, threading, encryption, audit) stays in one reviewed place.

## 7. Evidence over assertion

Security properties are expressed as tests: the suite decrypts every stored
value to prove erasure, forges tokens to prove rejection, tampers with the
audit table to prove detection, and dumps the database to prove nothing
personal is stored in clear. Reference tables in this documentation are
generated from the code and checked in CI. What is *not* verified is listed in
[Security posture](compliance/security-posture.md).

## 8. Boring technology

Python, PostgreSQL, standard OAuth 2.0 / OpenID Connect, JSON over HTTP. Few
dependencies, each one justified; no message broker, cache or search cluster to
operate and secure.
