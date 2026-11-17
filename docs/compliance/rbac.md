# Roles and permissions

Access is decided by three things, all enforced on the server:

1. **Roles** — assigned in your identity provider, delivered in the access
   token. Zentral stores no role assignments.
2. **Team scope** — which tickets a role holder can see.
3. **Personal-data masking** — whether they see requester data in clear.

A person or service with no Zentral role can sign in but can do nothing.

## Roles

| Role | Intended for |
|---|---|
| `Zentral.Agent` | Service-desk staff working tickets |
| `Zentral.Viewer` | Read-only stakeholders; sees no personal data |
| `Zentral.Admin` | Configuring teams, systems, channels and rules |
| `Zentral.Auditor` | Internal/external audit; sees the trail, not the tickets |
| `Zentral.PrivacyOfficer` | DPO function: access and erasure requests |
| `Zentral.Automation` | AI agents and scripts that triage; masked view, cannot contact requesters |
| `Zentral.Ingest` | Services that only submit issues |

Roles are additive: someone with two roles has the union of their permissions.

## Permission matrix

Generated from `src/zentral/security/rbac.py` — the code is the source of
truth, and CI fails if this table is out of date.

<!-- BEGIN GENERATED: rbac -->
| Permission | What it allows | Admin | Agent | Viewer | Auditor | PrivacyOfficer | Automation | Ingest |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `ticket:read` | Read tickets (within team scope) | ✓ | ✓ | ✓ |  | ✓ | ✓ |  |
| `ticket:read_pii` | See requester identity and unmasked content | ✓ | ✓ |  |  | ✓ |  |  |
| `ticket:read_all` | See tickets of every team | ✓ |  |  |  | ✓ |  |  |
| `ticket:triage` | See tickets not yet assigned to a team | ✓ | ✓ |  |  | ✓ | ✓ |  |
| `ticket:create` | Create tickets | ✓ | ✓ |  |  |  | ✓ |  |
| `ticket:update` | Change status, priority, category, system, tags; route to a team | ✓ | ✓ |  |  |  | ✓ |  |
| `ticket:assign` | Assign a ticket to a person | ✓ | ✓ |  |  |  |  |  |
| `ticket:reply` | Send a message to the requester | ✓ | ✓ |  |  |  |  |  |
| `ticket:note` | Add internal notes | ✓ | ✓ |  |  |  | ✓ |  |
| `team:manage` | Manage teams and membership | ✓ |  |  |  |  |  |  |
| `system:manage` | Manage the systems catalogue | ✓ |  |  |  |  |  |  |
| `source:manage` | Manage channels and their secrets | ✓ |  |  |  |  |  |  |
| `rule:manage` | Manage automation rules | ✓ |  |  |  |  |  |  |
| `template:manage` | Manage saved replies | ✓ |  |  |  |  |  |  |
| `audit:read` | Read and verify the audit trail | ✓ |  |  | ✓ | ✓ |  |  |
| `privacy:export` | Look up a person and export their data |  |  |  |  | ✓ |  |  |
| `privacy:erase` | Erase a person's data |  |  |  |  | ✓ |  |  |
| `privacy:restrict` | Record an objection to AI processing |  |  |  |  | ✓ |  |  |
| `ingest:write` | Submit issues as a service |  |  |  |  |  |  | ✓ |
<!-- END GENERATED -->

## Team scope

Holding `ticket:read` is necessary but not sufficient. A ticket is visible to a
principal only if at least one of these is true:

| Condition | Applies to |
|---|---|
| The principal has `ticket:read_all` | Admin, PrivacyOfficer |
| The ticket's team is one of the principal's teams | Everyone |
| The ticket is assigned to the principal | Everyone |
| The ticket has no team yet **and** the principal has `ticket:triage` | Agent, Admin, PrivacyOfficer, Automation |

Team membership comes from explicit assignment in Zentral or from an
identity-provider group linked to the team.

A ticket outside scope is reported as **not found** for every operation — its
existence is not revealed — and the attempt is audited as denied.

## Personal-data masking

Without `ticket:read_pii` (Viewer, Automation):

- the requester appears as "Requester 1a2b3c4d" with no e-mail address,
- subjects and message bodies pass through the redactor: e-mail addresses,
  phone numbers, card/IBAN/national-ID numbers, IP addresses, URLs and
  passwords become placeholders,
- looking up tickets by a person's e-mail address is refused.

Masking is pattern-based: it reduces exposure, it does not anonymise. Do not
give Viewer access to people who must not see ticket content at all.

## Segregation of duties

The mapping is built so that no single role can both act and cover its tracks:

| Conflict | How it is prevented |
|---|---|
| Configure the system **and** export or erase personal data | Admin has no `privacy:*` permission |
| Handle personal-data requests **and** change configuration | PrivacyOfficer has no `*:manage` permission |
| Review the trail **and** see or change ticket data | Auditor has only `audit:read` |
| Submit issues **and** read anything | Ingest has only `ingest:write` |
| An AI agent contacting people or seeing personal data | Automation lacks `ticket:reply`, `ticket:read_pii`, `ticket:assign` |

Tests assert each of these. For them to mean anything, **assign Admin, Auditor
and PrivacyOfficer to different people** — the [access review](../runbooks/access-review.md)
checks this.

Nobody — no role — can modify or delete audit events, or read stored connector
secrets.

## Machine identities

| Identity | How it authenticates | Scope |
|---|---|---|
| Service principal with a role | Access token (client credentials) | Its roles and teams |
| Webhook sender | Per-channel ingest token | Create messages on that channel only |
| Teams / Slack | Per-channel HMAC secret | Create messages on that channel only |
| Background worker | Internal; not reachable over the network | Recorded as `system:worker` in the audit trail |

## Changing the model

Roles and permissions are code, not configuration, so that they are reviewed,
tested and versioned. See
[Custom authentication](../extending/authentication.md#changing-roles-and-permissions).
