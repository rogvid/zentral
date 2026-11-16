# GDPR

How Zentral supports the obligations of a controller that uses it. This page
is technical documentation, **not legal advice**: your DPO decides whether the
measures are sufficient for your processing, and a DPIA is yours to carry out.

## Roles

You — the organisation running Zentral — are the **controller**. Zentral is
software you operate; if you self-host there is no processor for the service
desk itself. Sub-processors enter through the integrations you choose: your
identity provider, Microsoft 365 (mail, Teams), and — only if you configure
one — a cloud AI provider.

## What personal data is processed

| Data subjects | Data | Where |
|---|---|---|
| **Requesters** (employees, customers, anyone who writes in) | Name, e-mail address or chat ID; everything they write — potentially any category of data, including special categories | `contacts`, `tickets`, `messages` — encrypted |
| **Staff** (agents, admins) | Name, work e-mail, directory ID; their actions | `users`; audit trail; logs |

## Principles (Art. 5) and how they are implemented

| Principle | Measures in Zentral | Your part |
|---|---|---|
| **Lawfulness, fairness, transparency** | Privacy operations and all access are recorded; AI use is visible on every ticket | Legal basis; privacy notice mentioning the service desk and AI-assisted triage |
| **Purpose limitation** | Role and team scoping; no analytics or profiling features | Do not repurpose ticket data |
| **Data minimisation** | Plain text only, no attachments; personal data masked for roles that do not need it and before AI; audit and logs hold no content | Tell people not to send unnecessary data; keep tags free of names |
| **Accuracy** | Conversation history is preserved; corrections added as messages | — |
| **Storage limitation** | Automatic scrubbing after `ZENTRAL_RETENTION_DAYS`; people without tickets are erased | Choose and justify the period; mailbox and backup retention |
| **Integrity and confidentiality** | See [Security architecture](../architecture/security.md) | Platform security, key custody |
| **Accountability** | Tamper-evident audit trail; generated [permission matrix](rbac.md); this documentation | DPIA, records of processing, reviews |

## Data-subject rights

| Right | Support | How |
|---|---|---|
| Access (Art. 15), portability (Art. 20) | Built in | [Export](../guides/privacy-officer.md#right-of-access-and-portability-art-15-20) as JSON |
| Erasure (Art. 17) | Built in | [Erase](../guides/privacy-officer.md#right-to-erasure-art-17): identity removed, all content of their tickets replaced, lookup hashes dropped |
| Rectification (Art. 16) | Manual | Correcting message on the ticket |
| Restriction (Art. 18), objection (Art. 21) | Manual | Close tickets; see the [runbook](../runbooks/data-subject-request.md#rectification-and-objection) |
| Not to be subject to automated decisions (Art. 22) | By design | AI only suggests triage labels; it takes no decision about a person, and humans can change every label |

Procedure: [Data-subject request runbook](../runbooks/data-subject-request.md).

## Privacy by design and by default (Art. 25)

- Field-level encryption with keys outside the database.
- Identifiers that must be searchable are stored as keyed hashes, not in clear.
- Deny-by-default permissions; a dedicated permission for seeing personal data;
  the AI-agent role does not have it.
- Masking before AI is on by default and cannot be switched off for cloud
  providers in production.
- The default AI provider sends nothing anywhere.
- Retention is on by default (365 days).

## Security of processing (Art. 32)

| Measure | Implementation |
|---|---|
| Pseudonymisation and encryption | Field encryption; blind indexes; pseudonymous IDs in audit and logs |
| Confidentiality | Strong authentication via your IdP; RBAC; team scope; segregation of duties |
| Integrity | Hash-chained audit trail; append-only at the database; input validation |
| Availability and resilience | Stateless services; job retries; AI fallback; [backup and restore](../runbooks/backup-restore.md) |
| Regular testing | Automated security tests in CI; `zentral doctor`; scheduled audit verification; monthly restore test |

## AI and international transfers

| Provider | Transfer |
|---|---|
| `rules` | None |
| `openai_compatible` on your infrastructure | None |
| `anthropic`, `claude_cli` | Masked ticket text is sent to the provider. A processing agreement and a transfer assessment are required; **intended for test data** |

Masking is pattern-based and does not make text anonymous. For personal data,
use an on-prem model ([runbook](../runbooks/switch-to-on-prem-ai.md)). Every
classification's audit event records which provider was used and whether it was
on-prem, so you can evidence that no production data left.

## Records of processing (Art. 30) — input for your register

| Item | Suggested content |
|---|---|
| Purpose | Handling support and service requests; operational incident management |
| Categories of data subjects | Employees, customers, other requesters; service-desk staff |
| Categories of data | Contact details; request content (unstructured); staff identifiers and activity logs |
| Recipients | Service-desk staff by team; AI agents with masked view; (sub-processors per your integrations) |
| Retention | Ticket content: `ZENTRAL_RETENTION_DAYS` after resolution; audit trail: per your policy; backups: per your policy |
| Security measures | Reference this documentation |

## Breach notification (Art. 33, 34)

The [security incident runbook](../runbooks/security-incident.md) includes the
72-hour step and shows how to determine from the audit trail whose data an
account accessed.

## Backups

Erased and expired data persists in database backups until those backups
expire. This is generally accepted when backups are access-restricted, expire
on a defined short schedule, and erasures are re-applied after any restore —
which the [restore runbook](../runbooks/backup-restore.md#restore) requires.
Set your backup retention deliberately and document it.

## Known gaps to cover organisationally

- **The shared mailbox** keeps the original e-mails. Apply a retention policy
  to it in Exchange and include it in erasure requests.
- **Teams/Slack history** likewise.
- **Staff data** in the `users` table and audit trail has no automated
  retention; remove leavers' team memberships, and set an audit-retention
  policy ([audit runbook](../runbooks/audit-verification.md)).
- **Tags** are not encrypted: keep personal data out of them.
- **Requesters known only by chat ID** cannot be found by e-mail lookup.
- **Per-person opt-out of AI triage** is not available.
