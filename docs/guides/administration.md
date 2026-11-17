# Administration

For holders of the **Admin** role. Everything here is under *Administration*
in the web client and under `/api/v1` in the [REST API](api.md). Every change
is audited.

!!! info "What administrators cannot do"
    Assign roles (that happens in the identity provider), export or erase a
    person's data (privacy officers), or read connector secrets back (nobody
    can).

## Teams

A team is a queue. People see the tickets of their teams.

- **Create** a team with a name.
- **Add members** from the list of people who have signed in at least once.
- **Or link an identity-provider group**: put the group's object ID on the
  team and every member of that group belongs to the team automatically. The
  access token must contain the `groups` claim
  ([Entra setup](../operations/identity-entra.md#optional-group-based-teams)).

Tickets with no team sit in **triage**, visible to agents and administrators.

## Systems

The catalogue of business systems tickets can concern — see
[Systems catalogue](systems.md).

## Channels

A channel is one configured connection to the outside: a mailbox, a Teams
team, a webhook for your monitoring. Step-by-step guides:

- [E-mail (Microsoft 365)](connectors/email.md)
- [Microsoft Teams](connectors/teams.md)
- [Webhook (services)](connectors/webhook.md)

Each channel type has its own settings form. After saving, press **Test** to
run a live check; the card then shows **Healthy** with the time of the last
successful run, or **Failing** with the reason. **Edit** changes settings later.

For each channel you can set a **default team** and a **default system**.
Secrets you enter are encrypted and can never be displayed again — only
replaced (leave a field empty when editing to keep the stored value). Webhook tokens are shown once; use *Rotate token* if one is lost or
leaked. Disabling a channel stops intake immediately without deleting anything.

## Automation rules

Rules run on every new ticket, after AI classification, in ascending *Order*.

| When… | Then… |
|---|---|
| category is, priority is, system is, subject contains | route to team, set priority, set system, add tag |

Examples:

- *When category is security → route to Security, set priority high, tag
  `security-review`.*
- *When system is Payment platform → route to Payments team.*
- *When subject contains "refund" → set system Payment platform.* (Combine with
  the rule above by giving it a lower order number.)

Rules can be switched off and on without deleting them. Deleting anything in
*Administration* asks for confirmation first.

All conditions on a rule must match. An empty condition set matches every
ticket. Rules can only route and label.

!!! tip
    If a system has an **owning team**, tickets about it go there automatically
    when no team was set — you often do not need a rule at all.

## Saved replies

*Administration → Saved replies* holds the answers your desk gives every week.
A reply is offered to everyone, or only to one team's members. These
placeholders are filled in when an agent inserts it:

| Placeholder | Becomes |
|---|---|
| `{requester_first_name}` | The requester's first name ("there" when unknown or masked) |
| `{ticket_key}` | The ticket key, e.g. `ZEN-42` |
| `{agent_name}` | The agent's name |

Replies are encrypted at rest like ticket content. Keep personal data out of
them — they are shared text.

## Settings that live in configuration

Categories, service-level targets (`ZENTRAL_SLA_RESPONSE_HOURS`,
`ZENTRAL_SLA_RESOLVE_HOURS`), how long resolved tickets wait before closing
(`ZENTRAL_AUTO_CLOSE_DAYS`), the retention period, the AI provider, whether a
system is required before resolving, and all identity settings are deployment configuration, not
UI settings: see the [configuration reference](../operations/configuration.md).
This is deliberate — they are security- or compliance-relevant and should
change through your normal change process.
