# Privacy requests

For holders of the **PrivacyOfficer** role: handling data-subject requests
under GDPR. The operational checklist is the
[data-subject request runbook](../runbooks/data-subject-request.md); this page
explains the tools.

Open *Privacy requests* in the web client.

## Find the person

Enter the e-mail address. Zentral looks it up through a keyed hash — the
address itself is not stored in readable form, and it travels in the request
body so it does not end up in proxy logs. **Every lookup is audited**, whether
or not it finds anyone (the audit entry does not contain the address).

"No data held" means no requester with that address exists. People who only
ever wrote via Teams or Slack are identified by their directory ID, not their
e-mail address; ask an administrator to locate one of their tickets instead.

## Right of access and portability (Art. 15, 20)

*Export data (JSON)* downloads everything linked to the person:

- their identity record,
- every ticket they raised: subject, status, category, dates, the AI summary,
- every message to and from them.

Internal staff notes are **not** included by default: they are the desk's
working notes and may concern other people. Review them on the tickets
themselves if your assessment says they must be disclosed.

Treat the file as confidential, deliver it over a secure channel, and delete
your copy afterwards.

## Right to erasure (Art. 17)

*Erase personal data…* asks you to type `ERASE`, then:

- removes the person's name, address and lookup hash,
- replaces the subject and every message of all their tickets with
  `[erased]` — including staff notes and replies, which usually quote them,
- removes AI summaries, tags and channel thread references,
- closes those tickets.

What remains is an anonymous shell (status, category, system, dates) for
statistics. **This cannot be undone**, except by restoring a backup — which is
why your backup retention period matters for erasure
([GDPR](../compliance/gdpr.md#backups)).

The erasure is recorded in the audit trail with your identity, the time and
counts — that is your evidence of compliance. If the same person writes again
later, they are a new person to Zentral.

Before erasing, check whether you have a legal obligation or legitimate ground
to keep the data (for example an ongoing dispute); the tool does not make that
judgement for you.

## Rectification (Art. 16)

There is no bulk tool. A person's name is taken from their messages; correct
factual errors by adding a note or reply on the ticket.

## Retention

Independently of requests, tickets that have been resolved or closed for longer
than `ZENTRAL_RETENTION_DAYS` (default 365) are scrubbed automatically in the
same way, and people with no remaining tickets are erased. Check the audit
trail for `retention.ticket_scrubbed` to see it working.

## What you can see

You can read all tickets, including personal data, and the audit trail. You
cannot change configuration. Your own reads are audited like everyone else's.
