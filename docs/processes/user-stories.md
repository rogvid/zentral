# User stories

What each kind of user needs from Zentral, and where it stands. Stories marked
**new** were identified as missing in a review of the end-to-end processes and
have been built. Stories marked **not yet** are known gaps, ordered by how much
they matter; they are candidates for the roadmap, not promises.

✓ built · ✓ **new** built in the latest round · ◻ not yet

## Requester

| | As a requester, I want… | So that… | How |
|---|---|---|---|
| ✓ | to ask for help by e-mail or Teams, where I already am | I don't need to learn a tool | [Intake](intake.md) |
| ✓ **new** | to get a confirmation with a reference number when I e-mail | I know it arrived and can refer to it | Acknowledgement, per channel |
| ✓ | my reply to land on the same ticket | I don't have to explain twice | Threading |
| ✓ | a resolved issue to reopen if I say it isn't fixed | I'm not sent back to the start | 7-day grace period |
| ✓ | to see and get my data, or have it deleted | my rights are respected | [Data-subject requests](data.md#requests) |
| ◻ | a self-service page listing my open requests | I can check progress without writing | Not yet: needs a requester sign-in design |
| ◻ | to send a screenshot or log file | the desk sees what I see | Not yet: attachments need malware scanning and storage design |
| ◻ | to rate the help I got | the desk learns | Not yet |

## Agent

| | As an agent, I want… | So that… | How |
|---|---|---|---|
| ✓ | one inbox for every channel | nothing is missed | [Inbox](../guides/agents.md) |
| ✓ | tickets pre-labelled and routed | I start with the right ones | [Triage](triage.md) |
| ✓ **new** | to see which tickets are about to break a promise | I work on the right thing first | Overdue card, Due column, Targets panel |
| ✓ **new** | the clock to stop while I wait for the requester | I'm not blamed for their silence | Pending pauses the resolution clock |
| ✓ | internal notes that can never reach the requester | I can think aloud safely | Notes |
| ✓ | to see what happened to a ticket, by whom | handovers are easy | Activity |
| ✓ | drafts that survive switching tickets | I can look something up mid-reply | Composer |
| ◻ | to be notified when a ticket is assigned to me or becomes overdue | I don't have to keep checking | Not yet: needs a staff notification channel (e-mail or Teams) |
| ✓ **new** | saved replies for common answers | I answer faster and consistently | Saved replies, per team |
| ✓ **new** | to merge duplicate tickets | one problem is one ticket | [Merge](lifecycle.md#duplicates) |

## Team lead / administrator

| | As an admin, I want… | So that… | How |
|---|---|---|---|
| ✓ | to connect channels without code, and test them | setting up is safe and quick | Channels, *Test* |
| ✓ | mail access proven to be limited to one mailbox | a leaked secret can't read the CEO's mail | [Scope check](../guides/connectors/email.md#how-the-scope-check-works) |
| ✓ | a catalogue of the systems we support | every ticket says what it is about | [Systems](../guides/systems.md) |
| ✓ | rules that route tickets automatically | triage is consistent | Automation |
| ✓ **new** | response and resolution targets per priority | the desk makes promises it can measure | `ZENTRAL_SLA_*_HOURS` |
| ✓ **new** | to see volume, sources and target attainment | I can staff and fix the right things | [Insights](oversight.md#insights) |
| ✓ **new** | resolved tickets to close by themselves | the queue stays honest | Auto-close |
| ◻ | targets that follow business hours and holidays | an urgent ticket on Friday night is fair | Not yet: calendar hours today |
| ◻ | targets that differ per system or team | the payments platform can promise more | Not yet: per priority today |
| ◻ | escalation when a target is about to break | someone acts before it is late | Not yet: depends on notifications |

## Privacy officer

| | As a privacy officer, I want… | So that… | How |
|---|---|---|---|
| ✓ | to find everything about a person | I can answer an access request | Lookup, export |
| ✓ | to erase a person completely, provably | I can answer an erasure request | Erasure + audit |
| ✓ | data to disappear on schedule | we keep nothing longer than needed | [Retention](data.md#retention) |
| ✓ | AI to see only masked text, on our hardware | no personal data leaves | [AI pipeline](../architecture/ai.md) |
| ✓ **new** | to exclude one person from AI triage | I can honour an objection | [Objection](../guides/privacy-officer.md#objection-to-ai-processing-art-21) |

## Auditor

| | As an auditor, I want… | So that… | How |
|---|---|---|---|
| ✓ | every access and change recorded, including refusals | I can reconstruct what happened | [Oversight](oversight.md) |
| ✓ | proof that the record was not altered | the evidence holds | Hash chain, *Verify integrity* |
| ✓ | to do this without seeing ticket content | auditing doesn't widen access | Auditor role |
| ◻ | automated audit archiving and retention | the trail doesn't grow forever | Not yet: [manual runbook](../runbooks/audit-verification.md) |

## AI agent builder

| | As someone building an agent, I want… | So that… | How |
|---|---|---|---|
| ✓ | a standard protocol (MCP) and my existing identity provider | no special integration | [MCP](../guides/mcp.md) |
| ✓ | a least-privilege role | my agent can't leak or reply by mistake | `Zentral.Automation` |
| ✓ **new** | the same insights a team lead sees | the agent can report on the desk | `get_insights` tool |
