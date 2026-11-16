# Security incident

**Use when** you suspect unauthorised access, a leaked secret, tampering,
misuse by an insider, or a compromised host.

This runbook covers Zentral-specific actions. It plugs into — and does not
replace — your organisation's incident process.

!!! danger "The 72-hour clock"
    If personal data may have been accessed, lost or altered, GDPR Art. 33
    requires notifying the supervisory authority **within 72 hours of becoming
    aware**. Involve your DPO at step 1, not at step 6.

## 1. Declare and assemble

- Open an incident record; note the time you became aware.
- Involve: incident lead, platform operator, DPO / privacy officer, a Zentral
  administrator.
- Use a channel that does not depend on the systems in question.

## 2. Preserve evidence (before changing anything)

```bash
zentral audit verify                      # note the result
zentral audit export incident-audit.jsonl
pg_dump … --format=custom > incident-db.dump      # or a storage snapshot
```

Save the application, proxy, database and identity-provider sign-in logs for
the period. Store all of it where the suspected actor cannot reach it. The
database dump contains personal data (encrypted) — handle accordingly.

## 3. Contain

Pick the row that matches. All of these are reversible.

| Situation | Contain by |
|---|---|
| A **user account** is compromised or misused | Disable the account / revoke sessions in the identity provider; remove its Zentral role assignment. Tokens already issued stay valid until they expire (≤ 1 hour for Entra) — if that is too long, see "stop everything" |
| An **AI agent / service identity** is misbehaving or compromised | Remove its app-role assignment and delete its credentials in the identity provider; remove it from teams |
| A **webhook token** leaked | *Channels → Rotate token*, or disable the channel |
| A **channel credential** leaked (Graph secret, Teams secret) | Delete the secret at the source (Entra / Teams); disable the channel |
| **Keys** may have leaked | [Rotate](key-rotation.md) — encryption key first |
| **Database credentials** leaked | Change the password; review database logs for foreign connections |
| **Host or container** compromised | Isolate it from the network; do not delete it (evidence). Assume every secret it held is compromised: rotate all of them |
| **Stop everything** | Scale API and worker to zero / `docker compose stop api worker`. Intake by e-mail resumes later without loss; webhook senders will get errors |

## 4. Establish what happened

The audit trail answers most questions. As auditor (or with SQL):

| Question | Where to look |
|---|---|
| What did this identity do? | Audit: `actor_id = …` |
| Whose data did it read? | Audit: actions `ticket.read`, `ticket.messages.read`, `ticket.list`, `privacy.exported` for that actor → resource IDs are ticket numbers |
| Was it probing? | Audit: `outcome = denied` for that actor |
| Did anything change? | Audit: `ticket.updated`, `source.*`, `rule.*`, `team.*`, `system.*` |
| Was anything sent out? | Audit: `ticket.replied`, `message.delivered` |
| What did an AI agent do? | Audit: `channel = mcp` |
| Where did requests come from? | `ip` on audit events; proxy logs |
| Failed sign-ins or forged tokens? | Application log: `authentication_failed`, `hook_auth_failed`; identity-provider sign-in logs |
| Is the trail itself intact? | `zentral audit verify`; compare with the archive ([runbook](audit-verification.md)) |

To list the people whose data a compromised account read:

```sql
SELECT DISTINCT t.id AS ticket, t.requester_id
FROM audit_events a JOIN tickets t ON t.id::text = a.resource_id
WHERE a.actor_id = '<object-id>' AND a.resource_type = 'ticket'
  AND a.action IN ('ticket.read', 'ticket.messages.read')
  AND a.occurred_at BETWEEN '<from>' AND '<to>';
```

A privacy officer can then identify the individuals in the web client.

**Limits of the evidence:** list views are audited as a count, not per ticket —
assume every ticket in the actor's scope during the period was at least visible
by subject. Direct database access is not in Zentral's audit trail; use
database logs.

## 5. Assess the impact

With the DPO: whose data, which categories (support requests can contain
anything, including special categories), how many people, whether it left the
organisation, and the likely consequences. What was exposed depends on *what*
was compromised:

| Compromised | Exposed |
|---|---|
| A user or agent account | What its role and teams could read (masked, if without `ticket:read_pii`) |
| A database copy **without** keys | Metadata only: statuses, timestamps, team/system links, staff names, audit actor IDs and IPs |
| A database copy **with** the encryption key | Everything |
| The application host | Assume everything (keys are in memory) |
| A webhook token | Nothing readable — it can only create tickets |

## 6. Notify

Per your incident process and the DPO's assessment: supervisory authority
(72 hours), affected individuals if the risk is high (Art. 34), and anyone
contractually entitled.

## 7. Recover

- Rotate every secret that the compromised component could access.
- Restore integrity if data was altered: from backup
  ([runbook](backup-restore.md)), then re-apply erasures.
- Re-enable channels and identities one at a time.
- `zentral doctor` and `zentral audit verify` clean before declaring recovery.

## 8. Afterwards

Write the post-incident review: timeline, root cause, what detection missed,
what to change. Feed changes back into the
[threat model](../architecture/threat-model.md) and these runbooks. Report
vulnerabilities in Zentral itself as described in `SECURITY.md`.
