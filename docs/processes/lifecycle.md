# Ticket lifecycle

## States

```mermaid
stateDiagram-v2
    direction TB
    [*] --> new: arrives
    new --> open: someone takes it,<br/>or replies
    open --> pending: waiting on<br/>the requester
    pending --> open: requester replies
    open --> resolved: fixed
    pending --> resolved: fixed
    resolved --> open: requester writes<br/>within the grace period
    resolved --> closed: grace period ends<br/>(default 7 days)
    open --> closed: closed by hand
    closed --> [*]: content scrubbed<br/>after retention
```

| State | Meaning | Who moves it here | The clocks |
|---|---|---|---|
| **New** | Nobody has touched it | Zentral, on arrival | Both running |
| **Open** | Being worked | An agent (assigning or replying), or the requester (replying) | Both running |
| **Pending** | The desk is waiting for the requester | An agent | Resolution clock **paused** |
| **Resolved** | Fixed, but the requester can still say "it isn't" | An agent | Stopped; result recorded |
| **Closed** | Finished; a new message starts a new ticket | Zentral after the grace period, or an agent | Stopped |

## Targets

Every ticket makes two promises, set by its priority.

| Priority | First reply | Resolution |
|---|---|---|
| Urgent | 1 hour | 8 hours |
| High | 4 hours | 1 day |
| Normal | 8 hours | 3 days |
| Low | 1 day | 7 days |

These are the defaults (calendar hours); set your own with
`ZENTRAL_SLA_RESPONSE_HOURS` and `ZENTRAL_SLA_RESOLVE_HOURS`, e.g.
`urgent=0.5,high=2,normal=8`. A priority left out has no target.

```mermaid
flowchart LR
    C["Ticket created<br/>at 09:00, normal"] --> RD["Reply due 17:00"]
    C --> SD["Resolution due<br/>+3 days"]
    RD --> R1{"First reply<br/>by a person or agent?"}
    R1 -- "before 17:00" --> M1["Met"]
    R1 -- "after 17:00" --> L1["Met late"]
    R1 -- "not yet, past 17:00" --> B1["Overdue"]
    SD --> P{"Pending?"}
    P -- "yes, for 5 h" --> SH["Resolution due<br/>moves by 5 h"]
    P -- no --> R2{"Resolved?"}
    R2 -- "in time" --> M2["Met"]
    R2 -- "late" --> L2["Met late"]
    R2 -- "not yet, past due" --> B2["Overdue"]
```

The rules behind it:

- **Only a real reply counts.** An agent's or AI agent's reply to the requester
  meets the first-reply target. An internal note does not; neither does the
  automatic acknowledgement.
- **Waiting on the requester pauses the resolution clock**, and the paused time
  is added to the deadline when the ticket becomes active again — whether an
  agent reopens it or the requester replies.
- **Changing the priority re-plans both targets from the creation time.** Raising
  a ticket to urgent can make it overdue immediately; that is deliberate.
- **Internal tickets** (no requester) have no first-reply target.

Where people see it:

| Where | What |
|---|---|
| Inbox, *Overdue* card | Count and filter of active tickets past a target |
| Inbox, *Due* column | The next promise: "Reply in 40m", "Resolve in 2d", "Waiting on requester", "Reply overdue · 2h ago" |
| Ticket, *Targets* panel | Both targets, their state and exact times |
| Insights | Share met on time, medians, trend |

## Closing

```mermaid
flowchart LR
    R["Resolved"] --> G{"Requester writes<br/>within 7 days?"}
    G -- yes --> O["Reopened, same ticket"]
    G -- no --> C["Closed automatically<br/>(ticket.auto_closed)"]
    C --> N{"Requester writes<br/>later?"}
    N -- yes --> NT["New ticket —<br/>closed tickets never reopen"]
    C --> T{"Retention period<br/>passed?"}
    T -- yes --> S["Content scrubbed;<br/>statistics kept"]
```

The grace period is `ZENTRAL_AUTO_CLOSE_DAYS` (default 7; 0 turns automatic
closing off). It runs with the six-hourly housekeeping job, together with
[retention](data.md#retention).

## Who owns a ticket

| Phase | Owner | Can see it |
|---|---|---|
| Untriaged | Triage (agents with `ticket:triage`) | Agents, admins, privacy officers, AI agents |
| In a team | The team | That team's members, its assignee, admins, privacy officers |
| Assigned | The assignee, on behalf of the team | As above; the assignee keeps access even if it is re-routed |
| Resolved, closed | The team | As above |
| Scrubbed | Nobody — only statistics remain | As above, without content |

## Outcome

A resolved and eventually closed ticket, a requester who got an answer, an
honest record of whether it happened in time, and — after the retention
period — no personal data left behind.
