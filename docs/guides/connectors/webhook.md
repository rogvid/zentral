# Webhook (services)

The simplest way for a monitoring system, a CI pipeline or one of your own
applications to raise an issue: one authenticated HTTP request.

## Create the channel

*Administration → Channels → Add channel*, connector **Webhook**. Optionally
choose a default team and a default [system](../systems.md).

You are shown a **webhook URL** and an **ingest token** (`zis_…`). The token is
displayed once — Zentral keeps only a hash. Put it in the sending system's
secret store. It can do exactly one thing: create messages on this channel.

## Send

```bash
curl -X POST "https://servicedesk.example.com/api/v1/hooks/<source-id>" \
  -H "Authorization: Bearer zis_…" \
  -H "Content-Type: application/json" \
  -d '{
        "title": "Checkout returns HTTP 500",
        "description": "Error rate 34% since 09:40 UTC.",
        "severity": "critical",
        "system": "payments",
        "thread_key": "checkout-5xx",
        "external_id": "alert-2026-11-16-0940"
      }'
```

Response: `202 {"tickets": "ZEN-128"}`.

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | Ticket subject (≤ 200 characters) |
| `description` | no | Body text. Plain text; markup is not rendered |
| `severity` | no | `critical`/`urgent` → urgent · `high` → high · `warning`/`normal` → normal · `info`/`low` → low |
| `system` | no | Key of the [system](../systems.md) concerned. Unknown keys fall back to the channel default |
| `thread_key` | no | Messages with the same key are appended to the same open ticket — use an alert fingerprint so a flapping alert does not open fifty tickets |
| `external_id` | no | Idempotency key: the same ID is accepted once, retries are harmless. The `Idempotency-Key` header works too |
| `requester_email`, `requester_name` | no | If the issue is raised on behalf of a person |
| `context` | no | Up to 20 short facts — `{"page": "/orders/42", "app_version": "3.14"}` — shown under the description |
| `attachments` | no | Up to 3 files: `[{"filename": "screen.png", "content_base64": "…"}]`. Needs [attachments](../attachments.md) enabled; a refused file is dropped, the report is kept |

Building a "Report a problem" button into your own app? See
[Report an issue from your own app](../report-button.md) — the token must stay
on your server.

Unknown fields are ignored, so you can usually point an existing alert template
at Zentral and map two or three fields.

## Behaviour worth knowing

- A message on a **resolved** ticket with the same `thread_key` reopens it; on
  a **closed** ticket it opens a new one.
- Requests over 1 MiB are rejected with `413` — or, with attachments enabled,
  over 1 MiB plus room for three files.
- Any authentication problem — wrong token, unknown channel, disabled channel —
  returns the same bare `401`.
- Rate limit: `ZENTRAL_HOOK_RATE_LIMIT_PER_MINUTE` per channel and source
  address (default 120).

## Examples

=== "Prometheus Alertmanager"

    Alertmanager's native webhook format differs, so use a small relay or its
    templating proxy to post one request per alert:

    ```yaml
    title: "{{ .CommonAnnotations.summary }}"
    description: "{{ .CommonAnnotations.description }}"
    severity: "{{ .CommonLabels.severity }}"
    system: "{{ .CommonLabels.system }}"
    thread_key: "{{ .GroupKey }}"
    ```

=== "GitHub Actions"

    ```yaml
    - name: Report failed deployment
      if: failure()
      run: |
        curl -fsS -X POST "$ZENTRAL_HOOK_URL" \
          -H "Authorization: Bearer $ZENTRAL_INGEST_TOKEN" -H "Content-Type: application/json" \
          -d "{\"title\": \"Deployment failed: $GITHUB_REPOSITORY\", \"severity\": \"high\",
               \"system\": \"webshop\", \"external_id\": \"$GITHUB_RUN_ID\"}"
      env:
        ZENTRAL_HOOK_URL: ${{ vars.ZENTRAL_HOOK_URL }}
        ZENTRAL_INGEST_TOKEN: ${{ secrets.ZENTRAL_INGEST_TOKEN }}
    ```

=== "Python"

    ```python
    import httpx

    httpx.post(HOOK_URL, headers={"Authorization": f"Bearer {TOKEN}"}, json={
        "title": "Nightly payroll export failed",
        "description": str(error),
        "severity": "high",
        "system": "hr-portal",
        "external_id": f"payroll-{run_date}",
    }, timeout=10).raise_for_status()
    ```

## Rotate the token

*Channels → Rotate token* issues a new token and invalidates the old one
immediately. Do this when someone with access leaves, and whenever a token may
have been exposed.
