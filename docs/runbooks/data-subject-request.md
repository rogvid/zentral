# Data-subject request

**Use when** a person asks what you hold about them, asks for a copy, or asks
for deletion. **Who:** a privacy officer. **Deadline:** one month from receipt
(GDPR Art. 12(3)); log the date you received it.

The tools are explained in [Privacy requests](../guides/privacy-officer.md).

## 1. Receive and log

- If the request arrives as a ticket, an agent must **not** act on it: they add
  an internal note "DSR — handed to privacy officer" and route it to you.
- Record: date received, channel, what is requested, deadline.

## 2. Verify identity

Confirm the requester is the person the data is about, in proportion to the
sensitivity — typically by replying to the address on file, or through your
customer portal. Do not ask for more identification than you need. Do not send
an export to an address you have not verified.

## 3. Locate

*Privacy requests* → enter the address → **Find person**.

- **Found**: note the number of tickets.
- **No data held**: check alternative addresses the person may have used. If
  they contacted you only through Teams or Slack, they are identified by
  directory ID — ask an administrator to find one of their tickets.

Remember the other places a service desk leaves traces — these are outside
Zentral and your responsibility to cover:

- the **shared mailbox** itself (Zentral does not delete mail from it),
- Teams / Slack channel history,
- backups (see step 6),
- log platforms (actor IDs and IPs of *staff*, not requester content).

## 4a. Access / portability request

1. **Export data (JSON)**.
2. Review the file: it contains the person's messages and your replies.
   Redact information about **other people** if any appears.
3. Decide whether internal notes must be disclosed; they are not in the export.
4. Deliver over a secure channel. Delete your local copy.

## 4b. Erasure request

1. **Check for grounds to refuse or delay** (Art. 17(3)): legal obligation to
   retain, legal claims in progress. If the person has open tickets, agree with
   the team whether to resolve first.
2. Consider exporting first if you may need to evidence what was held.
3. **Erase personal data…** → type `ERASE`.
4. Confirm: the lookup now reports *No data held*; their tickets show
   `[erased]`.
5. Erase the same person's mail from the shared mailbox and other channels.

## 5. Evidence

The audit trail holds the proof — `privacy.contact_lookup`,
`privacy.exported`, `privacy.erased` — with your identity, the time and the
counts, and no personal data. Record the audit sequence numbers in your DSR
log.

## 6. Backups

Erased data remains in database backups until they expire.

- State this in your reply if asked; it is accepted practice provided backups
  are not used to restore the person's data and expire on a defined schedule.
- **If a backup taken before the erasure is ever restored, repeat the
  erasure** ([backup runbook](backup-restore.md#restore), step 6). Keep your
  DSR log outside Zentral so this is possible.

## 7. Reply

Answer the person within the deadline: what was done, or why not, and their
right to complain to the supervisory authority.

## Rectification and objection

- **Rectification (Art. 16)**: add a correcting note or reply on the ticket;
  free text cannot be edited after the fact.
- **Objection / restriction (Art. 18, 21)**: resolve and close their tickets;
  if they object to AI triage specifically, there is no per-person switch —
  their new tickets would be classified like any other. Handle such requests
  manually and consider whether the `rules` provider (no model) is appropriate
  for your installation.
