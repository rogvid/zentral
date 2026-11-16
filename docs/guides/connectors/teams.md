# Microsoft Teams

People raise an issue by @mentioning the service desk in a Teams channel; they
get the ticket number back in the thread.

This connector uses a Teams **Outgoing Webhook**: Teams signs every request
with a shared secret (HMAC-SHA256) that only Teams and Zentral know.

## Set up

1. **Create the channel in Zentral first** — but you need the secret from
   Teams, so do steps 2–3 and come back. Decide the default team and system.
2. In Teams: open the team → *Manage team* → *Apps* → *Create an outgoing
   webhook*.
    - **Name**: what people will @mention, e.g. `ServiceDesk`
    - **Callback URL**: `https://servicedesk.example.com/api/v1/hooks/<source-id>`
      — you get the source ID in step 4; enter a placeholder and edit it
      afterwards, or create the Zentral channel with a temporary secret and
      update it.
3. Teams shows a **security token** once. Copy it.
4. In Zentral: *Administration → Channels → Add channel*, connector **Teams**,
   paste the token as **Hmac secret**. Copy the webhook URL shown on the
   channel into the Teams webhook's callback URL.

Then, in any channel of that team:

> **@ServiceDesk** my laptop will not start after the update

Zentral answers in the thread: *Thanks — this is logged as ZEN-131.*

## How people are identified

Teams sends the sender's display name and directory object ID, not their
e-mail address. Zentral identifies the requester by that ID, so tickets from
the same person are linked — but a privacy lookup by e-mail will not find
someone who has only ever used Teams.

## Replies from agents (optional)

Outgoing webhooks can only answer the original request. To have agents' replies
posted back into Teams, create a **Workflows** webhook ("Post to a channel when
a webhook request is received") for the channel and store its URL as the
channel's **Reply webhook url** secret. Zentral only calls allow-listed
Microsoft hosts (`ZENTRAL_OUTBOUND_HOST_ALLOWLIST`).

Without it, replies on Teams tickets show delivery status *failed* — use notes
and answer in Teams directly.

## Limits

- Outgoing webhooks work in **team channels**, not private chats.
- Teams must be able to reach Zentral's public URL.
- Formatting is reduced to plain text; attachments and images are ignored.
- Teams expects an answer within a few seconds; Zentral stores the ticket and
  answers immediately, classification happens afterwards.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Teams says the webhook failed | Callback URL wrong or not reachable; check for `401` in Zentral's log (`hook_auth_failed`) — the secret does not match |
| `401` after re-creating the webhook | Teams issued a new token: update the channel's secret |
| No ticket, no error | The message had no text besides the mention |
