# Working tickets

For service-desk agents. You need the **Agent** role and membership of at
least one team.

![A ticket](../assets/ticket.png)

## The inbox

The cards at the top are live counts for *your* scope and double as filters:

| Card | Shows |
|---|---|
| **Active** | Everything new, open or pending |
| **Urgent** | Active tickets with urgent priority |
| **Unassigned** | Nobody has picked these up |
| **Assigned to me** | Your own queue |
| **No system yet** | Tickets nobody has linked to a system |

Below, filter by **system**, switch tabs, or search. Press ++slash++ to jump to
the search box. Search matches the subject or a ticket key (`ZEN-42`) among the
1,000 most recent tickets you can see.

You see tickets that belong to **your teams**, tickets **assigned to you**, and
**untriaged** tickets (no team yet). You cannot see other teams' tickets — not
by guessing a link either.

A coloured bar on the left of a row marks high (amber) and urgent (red)
priority. A purple label ending in "?" is an AI suggestion that has not been
applied.

## Working a ticket

- **Take it**: set *Assignee* to yourself. A new ticket becomes *open*.
- **Say which system it is about**: the *System* field. If AI suggested one,
  use *Set system to …* in the AI triage card.
- **Reply to requester** sends your message through the channel the ticket
  came from (e-mail, Teams, …). Its delivery status appears on the message.
- **Internal note** is visible to staff only and never leaves the service desk.
  The composer turns yellow so you cannot confuse the two.
- **Status**: *pending* while you wait for the requester (their reply reopens
  it), *resolved* when done. A reply to a resolved ticket reopens it; a reply
  to a closed ticket starts a new one.
- **Route**: change *Team* to hand the ticket to another queue. You keep access
  while it is assigned to you.

## The AI triage card

It shows what the model suggested and how confident it was. Suggestions with
high confidence are applied automatically to fields nobody had set; you can
always change them. The model saw a version of the text with personal
identifiers masked, and it cannot do anything except suggest.

## Good practice with personal data

- Everything you open is recorded in the audit trail with your name and the
  time. That protects you as much as the requester.
- Do not copy ticket content into chat tools, documents or other AI assistants.
- Do not put names or e-mail addresses in **tags** — tags are not encrypted.
- If a requester asks what you hold about them or wants it deleted, do not try
  to handle it yourself: pass it to your privacy officer
  ([Privacy requests](privacy-officer.md)).

## Appearance

Use the theme button at the bottom of the sidebar to switch between automatic
(follows your device), light and dark.
