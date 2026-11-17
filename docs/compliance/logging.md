# Logging and audit

Zentral keeps two separate records with different purposes.

| | Audit trail | Operational log |
|---|---|---|
| **Purpose** | Evidence: who did what to which data | Running the system |
| **Where** | `audit_events` table (+ mirrored to stdout) | stdout (JSON lines) |
| **Integrity** | Hash-chained, append-only, verifiable | None — protect it in your log platform |
| **Written** | In the same transaction as the action | Best effort |
| **Contains content?** | Never | Never |
| **Read by** | Auditor, Admin, PrivacyOfficer | Operators |

## The audit trail

### What is recorded

Every action that goes through Zentral:

- **changes** to tickets, teams, systems, channels and rules,
- **reads** of ticket content (opening a ticket, reading its conversation,
  listing tickets),
- **privacy operations** — lookups (including misses), exports, erasures,
  retention scrubbing,
- **AI activity** — each classification with provider, model, on-prem flag and
  result; each automation rule applied,
- **message flow** — ingested, replied, delivered, delivery failed,
- **denied attempts** — missing permission or outside team scope,
- **audit access itself** — reading and verifying the trail.

### Event fields

| Field | Content |
|---|---|
| `seq` | Gap-free sequence number |
| `occurred_at` | UTC timestamp |
| `actor_id`, `actor_kind`, `actor_roles` | Directory object ID; `user`, `service` or `system`; roles at the time |
| `action` | See below |
| `resource_type`, `resource_id` | e.g. `ticket`, `42` |
| `outcome` | `success`, `denied`, `failure` |
| `channel` | `api`, `mcp`, `hook`, `worker`, `cli` |
| `request_id`, `ip` | Correlation with the access log; client address |
| `details` | Field names and enumerated values only |
| `prev_hash`, `hash` | The chain |

`details` never contains message text, subjects, names or e-mail addresses. A
test dumps the whole trail and asserts this. Example:

```json
{"action": "ticket.updated", "resource_id": "42",
 "details": {"changes": {"status": ["open", "resolved"], "system_id": ["", "6f1c…"]}}}
```

### Actions

| Area | Actions |
|---|---|
| Tickets | `ticket.created`, `ticket.ingested`, `ticket.message_received`, `ticket.read`, `ticket.list`, `ticket.messages.read`, `ticket.updated`, `ticket.note_added`, `ticket.replied`, `ticket.acknowledged`, `ticket.auto_closed`, `ticket.merged`, `ticket.merge_received`, `ticket.escalated` |
| Delivery | `message.delivered`, `message.delivery_failed` |
| AI | `ticket.classified`, `ticket.classification_skipped`, `automation.rule_applied` |
| Privacy | `privacy.contact_lookup`, `privacy.exported`, `privacy.erased`, `privacy.ai_objection_recorded`, `privacy.ai_objection_withdrawn`, `retention.ticket_scrubbed`, `retention.contact_erased`, `contact.created` |
| Administration | `team.created`, `team.deleted`, `team.member_added`, `team.member_removed`, `system.created`, `system.updated`, `system.deleted`, `source.created`, `source.updated`, `source.tested`, `source.failing`, `source.token_rotated`, `source.deleted`, `rule.created`, `rule.updated`, `rule.deleted`, `template.created`, `template.updated`, `template.deleted` |
| Access control | `access.denied` |
| Audit | `audit.read`, `audit.verified` |

### Integrity

```text
hash(n) = HMAC-SHA256(audit key, hash(n-1) ‖ canonical JSON of event n)
```

- **Atomic.** The event is committed in the same database transaction as the
  action. An action cannot succeed unrecorded; if the audit write fails, the
  action is rolled back (tested).
- **Tamper-evident.** Changing, removing or reordering any event breaks every
  later hash. Because the chain is keyed, someone with database access but
  without the application's audit key cannot recompute a consistent chain.
- **Append-only.** On PostgreSQL, triggers reject `UPDATE`, `DELETE` and
  `TRUNCATE` on the table (tested against a real PostgreSQL).
- **Verifiable.** `zentral audit verify`, or *Verify integrity* in the web
  client. See the [runbook](../runbooks/audit-verification.md).

### Honest limits

- Truncating the **tail** of the chain is not detectable from the chain alone;
  keep an independent copy (SIEM mirror or periodic export) and watch that the
  highest sequence number never decreases.
- Whoever holds **both** the audit key and database write access can forge the
  trail. Keep the key in a secret manager with its own access log.
- **List views** are recorded as one event with a count, not one per ticket.
- **Failed authentication** (no valid identity) is in the operational log, not
  the audit trail.
- Actions taken **directly in the database** are outside it.
- There is **no automated retention** for audit events yet.

### Retention

Decide how long you must keep audit evidence (often 1–7 years depending on
your sector) and export to immutable storage accordingly. The trail contains
staff identifiers and IP addresses — personal data about your employees —
so "forever" is not a defensible default either.

## The operational log

Structured JSON on stdout; format and alerting are described in
[Monitoring](../operations/monitoring.md). Privacy rules enforced in code:

- request and response bodies are never logged,
- query strings are never logged (they can carry e-mail addresses),
- the request ID is generated by the server — a client cannot inject log lines,
- connector errors are reduced to a status code (a Graph error body can contain
  mail content),
- a scrubbing filter masks e-mail addresses, tokens and similar patterns in
  every string value as a last line of defence.

## Mapping to common control frameworks

| Control | Where |
|---|---|
| ISO 27001 A.8.15 Logging · A.8.16 Monitoring | This page; [Monitoring](../operations/monitoring.md) |
| ISO 27001 A.5.15–5.18 Access control | [Roles and permissions](rbac.md); [Access review](../runbooks/access-review.md) |
| ISO 27001 A.8.24 Use of cryptography | [Security architecture](../architecture/security.md#data-protection); [Key rotation](../runbooks/key-rotation.md) |
| ISO 27001 A.5.24–5.28 Incident management | [Security incident](../runbooks/security-incident.md) |
| ISO 27001 A.8.13 Backup | [Backup and restore](../runbooks/backup-restore.md) |
| GDPR Art. 5(2), 30, 32 | [GDPR](gdpr.md) |

The mapping shows where relevant measures are documented; it is not a statement
of certification.
