# Data protection impact assessment (DPIA)

A **pre-filled draft** of the DPIA required by GDPR Art. 35 for a deployment of
Zentral. The technical facts are filled in from the code and this
documentation; everything marked **➜ Controller** depends on *your* use and
must be completed, challenged and signed by you and your DPO. Copy this page
into your own DPIA template; it is a starting point, not legal advice.

Why a DPIA is likely required: a service desk processes, at scale, free text
written by anyone who contacts it. That text can contain **special categories
of data** (health, union membership, …) and data about **vulnerable people**,
and Zentral applies **automated classification** (AI) to it. Several of the
criteria in the EDPB guidelines (WP248) are therefore met.

| | |
|---|---|
| Deployment | ➜ Controller: organisation, environment, URL |
| Version assessed | Zentral as of this documentation |
| Prepared by / date | ➜ Controller |
| DPO consulted / date | ➜ Controller |
| Review date | ➜ Controller (at least yearly, and on every change listed under [Triggers for review](#triggers-for-review)) |

## 1. Description of the processing (Art. 35(7)(a))

### Nature

Zentral receives requests from e-mail (one Microsoft 365 mailbox per channel),
Microsoft Teams, webhooks from your own systems, a requester portal and AI
agents (MCP). It creates tickets, classifies them (category, priority,
affected system) with rules or a language model on masked text, routes them to
teams, and lets staff reply, attach files, merge and resolve. Every access to
ticket content is recorded in a tamper-evident audit trail.

How data flows: [Personal data](../processes/data.md). How each step works:
[How it works](../processes/index.md).

### Scope

| | Facts from Zentral | ➜ Controller |
|---|---|---|
| Data subjects | Requesters (anyone who writes in); staff | Who your requesters are: employees, citizens, customers, children? |
| Data | Name, e-mail address or chat ID; free text of requests and replies; attachments (images, PDF, text); ratings; staff ID, name, e-mail; staff actions; IP addresses in audit events | Which special categories you expect in practice |
| Volume | — | Tickets per month; number of requesters; number of staff |
| Retention | Content scrubbed `ZENTRAL_RETENTION_DAYS` after resolution (default 365); requesters without tickets erased; portal links minutes, sessions hours | Your retention period and its justification; audit, log and backup retention |
| Geography | Self-hosted: wherever you run it | Hosting location; any transfer (cloud AI, identity provider, Microsoft 365) |

### Context

➜ Controller: the relationship with requesters (employees? public?), their
reasonable expectations, whether they can avoid the service desk, prior
concerns or incidents, relevant codes of conduct.

### Purposes

Handling support and service requests and operational incidents; measuring the
desk's performance in aggregate (Insights). ➜ Controller: confirm, and state
that ticket data is not used for other purposes (e.g. staff performance
appraisal, profiling of requesters).

## 2. Necessity and proportionality (Art. 35(7)(b))

| Question | Facts from Zentral | ➜ Controller |
|---|---|---|
| Lawful basis | — | Art. 6 basis (e.g. contract, legal obligation, public task, legitimate interest); Art. 9 condition if special categories are expected |
| Minimisation | Masking for roles without `ticket:read_pii` and before AI; audit trail and logs hold no content; Insights aggregates only; attachments limited to images, PDF, text, metadata stripped | Which channels you enable; guidance to requesters not to send unnecessary data |
| Storage limitation | Automatic scrubbing and erasure; [retention](../processes/data.md#retention) | The period; the shared mailbox's and backups' own retention |
| Accuracy | Conversation history preserved; corrections as new messages | — |
| Transparency | AI use visible on every ticket; acknowledgements | Privacy notice covering the service desk and AI-assisted triage |
| Rights | Access (export), erasure, objection to AI processing — built in; [runbook](../runbooks/data-subject-request.md) | Who handles requests; identity verification |
| Processors / transfers | None for Zentral itself when self-hosted; sub-processors only through integrations | DPAs with Microsoft, your IdP, hosting, and any cloud AI provider; transfer assessments |
| Automated decisions (Art. 22) | AI only *suggests* labels and routing; no decision with legal or similarly significant effect is taken automatically; low-confidence results are stored as suggestions | Confirm no such decisions are built on top |

## 3. Risks to the rights and freedoms of data subjects (Art. 35(7)(c))

Likelihood and severity are pre-assessed for a typical internal IT/HR service
desk **with the measures in place**; ➜ Controller: re-assess for your context.

| # | Risk | Source | Measures in Zentral | Likelihood | Severity | Residual |
|---|---|---|---|---|---|---|
| R1 | Unauthorised access to request content by staff outside the handling team | Insider curiosity | Team scope; role-based masking; every read audited; out-of-scope attempts audited | Possible | Significant | Low |
| R2 | Disclosure through a stolen database, backup or replica | External attacker, operator error | Field encryption; keys in a KMS-wrapped [keyring](../operations/key-custody.md), not in configuration or backups | Unlikely | Maximum | Low |
| R3 | Disclosure through compromise of the application host | External attacker | Hardened container, network policies; keys unwrapped only in memory; KMS access revocable and logged | Unlikely | Maximum | **Medium** — process memory holds the keys |
| R4 | Content sent to an external AI provider | Configuration | Rules or on-prem model by default; cloud providers get masked text only; masking cannot be disabled for them in production; per-ticket provenance in the audit trail | Unlikely (on-prem) | Significant | Low on-prem; **Medium** with a cloud provider (masking is pattern-based) |
| R5 | Malicious attachment harms staff devices | External sender | Type allow-list, metadata stripping, ClamAV scan before anyone can open it; PDFs download only | Possible | Significant | Medium — signature-based scanning |
| R6 | Wrong person gains portal access | Account takeover of the requester's mailbox; link forwarding | One-time links (minutes), hashed; sessions HttpOnly/SameSite=Strict; rate limits; same answer for known and unknown addresses | Unlikely | Significant | Low |
| R7 | Data kept longer than necessary | Process failure | Automatic scrubbing and erasure, audited | Unlikely | Limited | Low — but the shared mailbox and backups need their own retention (➜ Controller) |
| R8 | Data-subject rights cannot be honoured | Process failure | Export, erasure and objection built in; audited | Unlikely | Significant | Low |
| R9 | Audit trail altered to hide access | Insider with database access | Keyed hash chain; append-only triggers; signed archives; daily verification | Unlikely | Significant | Low |
| R10 | Requester identity revealed by e-mail address lookups | Attacker with a database copy *and* the blind-index key | Keyed blind index; key in the keyring | Remote | Significant | Low |
| R11 | Loss of availability or integrity (data loss) | Failure, ransomware | Backups with tested restore ([drill](../runbooks/backup-restore.md#test-the-restore-monthly)); offline key escrow | Possible | Significant | Low with monthly drills |
| R12 | Unknown vulnerabilities | Software defects | Automated security tests in CI; dependency audits; [independent test before go-live](pentest-brief.md) | Possible | Maximum | **Unknown until the penetration test is done** |

## 4. Measures to address the risks (Art. 35(7)(d))

Technical measures implemented in Zentral: see
[Security architecture](../architecture/security.md),
[Threat model](../architecture/threat-model.md) and
[Security posture](security-posture.md).

Organisational measures — ➜ Controller, at least:

- [ ] Independent penetration test done; high and critical findings fixed and re-tested ([brief](pentest-brief.md))
- [ ] Admin, Auditor and PrivacyOfficer are different people; quarterly [access review](../runbooks/access-review.md)
- [ ] Key custody with a KMS; escrow copy in a safe with two-person access
- [ ] On-prem or rules-based AI for personal data (or a DPA and transfer assessment for a cloud provider)
- [ ] Shared mailbox and Teams retention policies aligned with Zentral's
- [ ] Backup retention set and documented; monthly restore drill recorded
- [ ] Privacy notice updated; staff instructed (no names in tags, what not to ask for)
- [ ] Incident process includes the 72-hour notification ([runbook](../runbooks/security-incident.md))
- [ ] Residual risks R3, R4 (if cloud AI), R5 and R12 accepted in writing by the risk owner

## 5. Outcome and sign-off

| | ➜ Controller |
|---|---|
| Residual risk acceptable? | |
| Prior consultation with the supervisory authority needed (Art. 36)? | Only if high residual risk remains |
| DPO advice | |
| Decision and signature (risk owner) | |

## Triggers for review

Review this DPIA when any of the following changes: a new intake channel; a
change of AI provider or model; a cloud AI provider is enabled; new data
categories (e.g. a new connector or form); hosting location or key custody;
retention period; a significant security finding or incident; a new
population of requesters (e.g. opening the portal to the public).
