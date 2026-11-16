# Access review

**Use:** quarterly, and whenever someone changes role or leaves.
**Who:** the Zentral administrator with the identity administrator; sign-off by
the system owner.

Zentral stores no role assignments — roles come from the identity provider —
so most of the review happens there. Zentral holds team memberships, channel
credentials and the evidence of what people actually did.

## 1. Roles (identity provider)

Export the role assignments (*Entra: Enterprise applications → Zentral API →
Users and groups*) and check, role by role:

| Check | Why |
|---|---|
| Every holder still needs the role | Least privilege |
| **Admin**, **Auditor** and **PrivacyOfficer** are different people | Segregation of duties |
| At least two admins; no more than necessary | Availability vs. exposure |
| No person holds `Zentral.Ingest` or more than one of Agent/Viewer | Roles add up — the union applies |
| Service identities hold exactly one narrow role | `Zentral.Automation` or `Zentral.Ingest`, never Admin |
| Leavers and disabled accounts are gone | — |
| If you use `ZENTRAL_ROLE_MAP`: the mapped groups' membership | The map is part of your access model |

Also confirm *Assignment required* is still **Yes**, and that Conditional
Access (MFA) applies to the application.

## 2. Teams (Zentral)

*Administration → Teams*: for each team, is every member still on that team in
reality? Remove leavers — their account may be disabled, but the membership
row should not wait for them to come back in another role. Review the groups
linked to teams.

## 3. Machine access (Zentral)

*Administration → Channels*:

- Is every channel still needed? Disable or delete the rest.
- Who holds each webhook token? Rotate tokens whose holder is unknown.
- When do the e-mail connector's client secrets expire?

List AI agents and scripts (service principals with a Zentral role): is each
one still in use, and does someone own it?

## 4. What actually happened (audit trail)

As auditor, sample the period:

```sql
-- Who is active, and how
SELECT actor_id, actor_kind, count(*) AS events,
       count(*) FILTER (WHERE outcome = 'denied') AS denied,
       max(occurred_at) AS last_seen
FROM audit_events WHERE occurred_at > now() - interval '90 days'
GROUP BY 1, 2 ORDER BY events DESC;

-- Privileged operations
SELECT occurred_at, actor_id, action, resource_type, resource_id
FROM audit_events
WHERE occurred_at > now() - interval '90 days'
  AND (action LIKE 'privacy.%' OR action LIKE 'source.%' OR action LIKE 'team.%'
       OR action LIKE 'rule.%' OR action LIKE 'system.%')
ORDER BY occurred_at;
```

Look for: role holders with **no activity** (remove the role), identities with
many **denied** attempts (talk to them), privacy operations without a matching
entry in the DSR log, configuration changes nobody recognises.

## 5. Record

File the review: date, reviewers, assignments confirmed, changes made, findings.
This record is what you show an external auditor.

## Leaver checklist (do not wait for the quarterly review)

- [ ] Account disabled in the identity provider (ends access within the token
      lifetime, ≤ 1 hour)
- [ ] Zentral role assignment removed
- [ ] Removed from Zentral teams
- [ ] Open tickets reassigned (inbox → filter by assignee)
- [ ] If they had operator access: rotate the keys and secrets they could read
      ([runbook](key-rotation.md))
