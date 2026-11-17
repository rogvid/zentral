# Backup and restore

## What must be backed up

| What | Where it is | Notes |
|---|---|---|
| **The PostgreSQL database** | Your database server / the `db-data` volume | Everything: tickets, audit trail, configuration, job queue |
| **The three keys** | Your secret manager | `ZENTRAL_ENCRYPTION_KEYS`, `ZENTRAL_BLIND_INDEX_KEY`, `ZENTRAL_AUDIT_HMAC_KEY` (and any retired keys) |
| Deployment configuration | Your Git repository / `.env` | Reproducible, but saves time |

!!! danger "Keys and database go in different places"
    A database backup is useless without the encryption key — and that is the
    point: store them **separately**, so that whoever obtains a backup cannot
    read it. But losing the key means losing the data, so the key needs its own
    reliable backup (vault with its own recovery procedure, plus an offline
    copy in a sealed envelope).

Nothing else holds state: containers are disposable.

## Back up

Use your database platform's native backups with point-in-time recovery where
available. Otherwise:

=== "Docker Compose"

    ```bash
    docker compose exec -T db pg_dump -U zentral -d zentral --format=custom \
      > "zentral-$(date -u +%Y%m%dT%H%M%SZ).dump"
    ```

=== "Any PostgreSQL"

    ```bash
    pg_dump "postgresql://zentral@db.example.internal/zentral?sslmode=require" \
      --format=custom --file "zentral-$(date -u +%Y%m%dT%H%M%SZ).dump"
    ```

- Schedule it at least daily; keep the WAL for point-in-time recovery if a
  day's loss is too much.
- Encrypt backups at rest and restrict access: although content is encrypted,
  metadata (who, when, status, staff names) is not.
- **Retention of backups counts for GDPR**: erased data survives in backups
  until they expire. Keep the backup retention short and documented
  ([GDPR](../compliance/gdpr.md#backups)).

## Restore

**Use when:** data loss, corruption, failed upgrade, or rebuilding in a new
environment.

1. **Stop** API and worker (so nothing writes during the restore).

2. **Restore the database** into an empty database:

    === "Docker Compose"

        ```bash
        docker compose stop api worker
        docker compose exec -T db dropdb -U zentral zentral
        docker compose exec -T db createdb -U zentral zentral
        docker compose exec -T db pg_restore -U zentral -d zentral --no-owner < zentral-20261116T020000Z.dump
        ```

    === "Any PostgreSQL"

        ```bash
        createdb zentral_restored
        pg_restore -d zentral_restored --no-owner zentral-20261116T020000Z.dump
        ```

3. **Provide the keys that were in use when the backup was taken.** If keys
   were rotated since, put the old encryption key in the list as well.

4. **Check before starting:**

    ```bash
    zentral doctor
    ```

    | Line | If it fails |
    |---|---|
    | `database: … schema NNNN` | Backup is from an older version: run `zentral db upgrade` |
    | `encryption` — *cannot decrypt existing data* | Wrong encryption key for this backup |
    | `audit trail` — *chain broken at event 1* | Wrong audit key (add the old one to `ZENTRAL_AUDIT_HMAC_PREVIOUS_KEYS`) |

5. **Start** API and worker. Sign in and open a ticket to confirm content is
   readable.

6. **Re-apply erasures.** Any erasure performed *after* the backup was taken
   has been undone by the restore. List them from your records (privacy
   officers are notified of every erasure — see
   [Monitoring](../operations/monitoring.md)) and repeat them. This is a GDPR
   obligation, not a nicety.

7. **Catch up.** E-mail received since the backup is re-ingested automatically
   (the mailbox still has it and the cursor was restored). Webhook and Teams
   messages from the gap are lost; tell the teams concerned.

## Test the restore — monthly

A backup you have not restored is a hope. Once a month, restore the latest
backup into a scratch database and run:

```bash
ZENTRAL_DATABASE_URL=postgresql+asyncpg://…/zentral_restore_test zentral doctor
```

`doctor` proves the three things that matter: the schema is intact, the key
decrypts the data, and the audit chain verifies. Record the date and result.
Drop the scratch database afterwards — it contains personal data.

## Disaster recovery in a new environment

1. Deploy Zentral per the [first-deployment runbook](first-deployment.md), steps
   1–3, using the **original keys** and identity settings.
2. Restore the database as above.
3. If the public hostname changed: update `ZENTRAL_PUBLIC_BASE_URL`,
   `ZENTRAL_ALLOWED_HOSTS`, the redirect URI in the identity provider, and the
   webhook URLs configured in Teams and in your sending systems.
