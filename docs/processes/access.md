# People and access

## How people come in

Zentral keeps no passwords and assigns no roles. People and services arrive
through your identity provider; Zentral only reads what the access token says.

```mermaid
flowchart TD
    subgraph IDP["Identity provider (Entra ID or OIDC)"]
        U["Person or service"] --> S["Signs in<br/>(MFA, conditional access)"]
        S --> T["Access token:<br/>subject · roles · groups"]
    end
    T --> V{"Zentral validates:<br/>signature, issuer, audience,<br/>expiry, tenant, scope"}
    V -- invalid --> X["401 — nothing<br/>else happens"]
    V -- valid --> P["Principal = roles → permissions"]
    P --> TM["Teams: explicit membership<br/>+ linked directory groups"]
    TM --> SC["Scope: own teams · assigned to me<br/>· triage (if allowed) · all (if allowed)"]
    SC --> M{"ticket:read_pii?"}
    M -- yes --> FULL["Sees requester identity<br/>and full text"]
    M -- no --> MASK["Sees masked text,<br/>pseudonymous requester"]
```

| Question | Answer |
|---|---|
| Who decides *what* someone may do? | The **identity administrator**, by assigning one of seven roles |
| Who decides *which tickets* they see? | The **Zentral admin**, by team membership (or a linked directory group) |
| What does Zentral store about staff? | Object ID, display name and e-mail, refreshed at sign-in — so they can be shown as assignee or author |
| How long does a removed role keep working? | Until the person's access token expires (typically under an hour) |

## Joiners, movers, leavers

```mermaid
flowchart LR
    J["Joiner"] --> J1["Identity admin assigns a role"] --> J2["Person signs in once"] --> J3["Zentral admin adds them to a team<br/>(or they are in a linked group)"]
    M["Mover"] --> M1["Change team membership<br/>(or directory group)"] --> M2["Reassign their open tickets"]
    L["Leaver"] --> L1["Disable the account<br/>in the identity provider"] --> L2["Remove role and team membership"] --> L3["Reassign open tickets"]
```

The detailed checklist is in the [access review runbook](../runbooks/access-review.md#leaver-checklist-do-not-wait-for-the-quarterly-review).

## Requesters

People who ask for help never get a staff account. In the
[requester portal](../guides/requester-portal.md) they prove who they are by
opening a one-time link sent to the address they used, and then see only their
own requests and the public conversation.

```mermaid
flowchart LR
    R["Requester"] --> L["Asks for a link<br/>(same answer for everyone)"]
    L --> K{"Known requester?"}
    K -- no --> N["Nothing sent;<br/>attempt recorded"]
    K -- yes --> E["One-time link e-mailed<br/>(15 min, once)"]
    E --> S["Session cookie<br/>(HttpOnly, portal only, 8 h)"]
    S --> V["Own requests,<br/>public conversation only"]
```

## Machines are people too (almost)

| Caller | Credential | What it may do |
|---|---|---|
| AI agent (MCP) | Token for its own identity, usually `Zentral.Automation` | Triage and annotate masked tickets; never reply to requesters |
| Script / integration | Token for its own identity, a narrow role | Whatever that role allows |
| Monitoring system | Per-channel ingest token | Create messages on that one channel |
| Teams / Slack | Per-channel signing secret | Create messages on that one channel |
| Zentral's worker | Internal only, never reachable over the network | Recorded as `system:worker` |
| Requester (portal) | One-time e-mail link, then a portal-only session | Their own requests; recorded as `contact:<id>` |

## Outcome

Everyone sees exactly what their role and teams allow, and every action is
attributed to a stable identity in the audit trail.
