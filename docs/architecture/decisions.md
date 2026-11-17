# Decision records

Short records of the choices that shape Zentral, with the alternatives that
were rejected. Add a record when you reverse or extend one.

## ADR 1 — Resource server only; no local accounts

**Decision.** Zentral validates access tokens from an external identity
provider and has no user table with credentials, no sessions and no role
storage.

**Why.** Joiners, movers and leavers, MFA, conditional access and credential
hygiene are solved problems in the identity provider. A second copy in the
application is a second thing to breach and to forget to update.

**Consequence.** A provider is required even for small installations. Staff
appear in assignee lists only after their first sign-in.

## ADR 2 — Pluggable identity through profiles

**Decision.** An `IdentityProfile` (issuer, audiences, algorithms, claim names,
role map) describes a provider. Entra ID and generic OIDC are built-in
profiles; a custom `Authenticator` can be injected.

**Why.** The first version was Entra-only. Requiring Entra would exclude
organisations on Keycloak, Okta or AD FS, while per-provider code paths would
multiply the security-critical surface. One validator, parameterised, keeps one
set of checks.

**Rejected.** Using a vendor SDK in the browser (it tied the web client to one
provider and added ~280 kB); the web client now uses a 150-line standard PKCE
client for all providers.

## ADR 3 — Field-level encryption in the application

**Decision.** Personal data is encrypted by the application with keys the
database never sees.

**Why.** Database-level encryption (TDE) protects against stolen disks but not
against a leaked dump, a misconfigured replica or a curious DBA.

**Consequence.** See ADR 4. A compromised application host still exposes data,
because it holds the keys — documented in the threat model.

## ADR 4 — No full-text index over ticket content

**Decision.** Subjects and bodies are not indexed in clear. Search decrypts in
the application over a bounded window of recent tickets; lookups by person use
a blind index.

**Why.** A search index is a second, unencrypted copy of the data ADR 3
protects.

**Rejected.** PostgreSQL full-text search over plaintext columns (defeats
ADR 3); an external search cluster (another system holding personal data).

**Revisit when** search over older tickets becomes a real need: options are a
tokenised blind index or a search service inside the same trust boundary.

## ADR 5 — The audit trail is a keyed hash chain in the primary database

**Decision.** Audit events live in PostgreSQL, chained with HMAC-SHA256,
written in the same transaction as the action, protected by triggers.

**Why.** Same-transaction writes give the strongest guarantee: no action
without a record. A separate log service would make that a distributed
transaction.

**Consequence.** Chain appends are serialised with an advisory lock, which
caps write throughput at a level far above a service desk's needs. Events are
also mirrored to stdout for a SIEM.

## ADR 6 — A database-backed job queue

**Decision.** Background jobs are rows claimed with `FOR UPDATE SKIP LOCKED`.

**Why.** Transactional enqueueing (ADR 5's guarantee, applied to jobs) and one
less component to run and secure. **Revisit** above roughly a hundred jobs per
second.

## ADR 7 — AI suggests; deterministic rules act

**Decision.** The classifier returns a small validated object. It has no tools.
Routing is done by administrator-written rules.

**Why.** It makes prompt injection a labelling problem instead of a security
problem, and it makes behaviour explainable to an auditor.

## ADR 8 — Plug-ins never touch the database

**Decision.** Connectors return normalised messages; AI providers return a
classification. The core does persistence, encryption and audit.

**Why.** Third-party code should not be able to skip a security control by
omission. **Consequence.** Plug-ins are small (the Slack example is ~100
lines).

## ADR 9 — Systems are a catalogue, not free text

**Decision.** The system a ticket concerns is a reference to an
administrator-managed catalogue entry, settable by sender, channel, AI, rule or
person, in that order of precedence.

**Why.** A fixed vocabulary makes reporting and routing reliable and gives the
AI an allow-list to choose from (ADR 7).

## ADR 10 — Plain text only

**Decision.** HTML is reduced to text at ingest; attachments are not ingested.

**Why.** It removes whole classes of attack (stored XSS, malicious documents)
at the cost of formatting. **Revisit** attachments together with a
malware-scanning and storage design.
