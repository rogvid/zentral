# Monitoring and logs

## Health endpoints

| Endpoint | Meaning | Use as |
|---|---|---|
| `GET /healthz` | The process is running | Liveness probe |
| `GET /readyz` | Database reachable **and** schema at the expected revision | Readiness probe, load-balancer check |

## Logs

Both processes write **JSON lines to stdout**. Collect them with your platform's
log agent. Three kinds of line, distinguished by `logger`:

=== "Access log — `zentral.access`"

    ```json
    {"event":"request","method":"PATCH","path":"/api/v1/tickets/42","status":200,
     "duration_ms":11.2,"actor_id":"3f0c…","request_id":"9d2e…","level":"info",
     "timestamp":"2026-11-16T09:41:07.123Z"}
    ```

    One per request. **Query strings are never logged** (they can contain
    e-mail addresses).

=== "Audit mirror — `zentral.audit`"

    ```json
    {"event":"audit","seq":1285,"action":"ticket.updated","outcome":"success",
     "actor_id":"3f0c…","actor_kind":"user","resource_type":"ticket",
     "resource_id":"42","channel":"api","request_id":"9d2e…"}
    ```

    A copy of every audit event, for near-real-time shipping to a SIEM. The
    authoritative record is the database ([Logging and audit](../compliance/logging.md)).

=== "Application events"

    ```json
    {"event":"authentication_failed","error":"AuthenticationError","cause":"ExpiredSignatureError","check":"token_rejected","ip":"203.0.113.9","level":"warning"}
    {"event":"hook_auth_failed","source_id":"7a1e…","ip":"203.0.113.9","level":"warning"}
    {"event":"job_failed","kind":"send_message","attempt":2,"error":"ConnectorError","level":"warning"}
    {"event":"ai_provider_failed","provider":"openai_compatible","error":"AIError","cause":"ConnectError","level":"warning"}
    {"event":"jwks_refresh_failed","error":"ConnectTimeout","level":"error"}
    ```

`request_id` ties an access-log line to the audit events it produced, and is
returned to clients as `X-Request-ID`.

Logs contain actor IDs and IP addresses — personal data about your staff. Apply
your normal retention and access rules to the log platform. Message content,
subjects and secrets are never logged, and a scrubbing filter masks anything
that looks like an e-mail address or token as a safety net.

## What to alert on

| Signal | Query (conceptually) | Why |
|---|---|---|
| Not ready | `/readyz` ≠ 200 for 2 min | Database down or migration pending |
| Server errors | `event=request status>=500` rate > 0 | Bugs or database trouble |
| Sign-in problems | `event=jwks_refresh_failed` | Nobody can authenticate once cached keys expire |
| Brute force / misconfiguration | `event=authentication_failed` or `hook_auth_failed` spike per IP | Attack, or an expired secret in a sender |
| Denied access | `event=audit outcome=denied` spike per `actor_id` | Someone probing beyond their role |
| Stuck work | `event=job_failed` repeated; or SQL below | Channel credentials expired, IdP/Graph/model down |
| AI degraded | `event=ai_provider_failed` sustained | Model server down — triage quality drops to keyword rules |
| Privacy operations | `action=privacy.erased` / `privacy.exported` | Notify the DPO of every use |
| Audit integrity | `zentral audit verify` exit code ≠ 0 (scheduled) | Tampering — [incident runbook](../runbooks/security-incident.md) |

## Queue and data checks (SQL, read-only role)

```sql
-- Backlog and failures
SELECT kind, status, count(*), min(created_at) AS oldest FROM jobs
WHERE status IN ('pending', 'running', 'failed') GROUP BY kind, status;

-- Replies that could not be delivered
SELECT ticket_id, created_at FROM messages WHERE delivery_status = 'failed' ORDER BY created_at DESC;

-- Is polling alive? (cursor should move when mail arrives)
SELECT name, connector, enabled, cursor FROM ingest_sources;

-- Volume
SELECT date_trunc('day', created_at) AS day, count(*) FROM tickets GROUP BY 1 ORDER BY 1 DESC LIMIT 14;
```

A healthy queue has no `failed` rows and no `pending` row older than a few
minutes (except `retention_sweep`, which is scheduled six hours ahead).

## Scheduled checks

| How often | Check |
|---|---|
| Daily | `zentral audit verify` (cron / CronJob) |
| Weekly | `zentral doctor` |
| Monthly | Restore test ([backup runbook](../runbooks/backup-restore.md)) |
| Quarterly | [Access review](../runbooks/access-review.md) |
| Before expiry | Channel client secrets, TLS certificates |

## Metrics

There is no built-in metrics endpoint yet. Derive request rate, latency and
error rate from the access log (every line has `status` and `duration_ms`), or
from your proxy.
