# Rotate keys and secrets

| Secret | Rotate routinely | Rotate immediately when |
|---|---|---|
| [Encryption key](#encryption-key) | Yearly | It may have leaked; an operator with access leaves |
| [Audit key](#audit-key) | Yearly | It may have leaked |
| [Blind-index key](#blind-index-key) | No (see below) | It leaked *together with* a database copy |
| [Webhook ingest tokens](#webhook-ingest-tokens) | Yearly | Leaked; sender decommissioned |
| [Channel secrets](#channel-secrets) (Graph, Teams) | Before expiry | Leaked |
| [Database password](#database-password) | Per your policy | Leaked |

Take a backup first. Run `zentral doctor` before and after every rotation.

## Encryption key

Zero downtime. The key setting is a list: the **first** key encrypts, **all**
keys decrypt.

1. Generate a key and store it in your vault:

    ```bash
    zentral keys generate | head -1
    ```

2. **Prepend** it:

    ```bash
    ZENTRAL_ENCRYPTION_KEYS=<new-key>,<old-key>
    ```

3. Restart API and worker. New data is now written with the new key; old data
   is still readable.

4. Re-encrypt existing data:

    ```bash
    zentral keys rotate
    # re-encrypted 18432 values
    ```

5. Remove the old key from the setting, restart, and verify:

    ```bash
    zentral doctor      # ✓ encryption: 1 key(s) configured
    ```

6. Keep the old key in the vault until every backup taken before step 4 has
   expired — you need it to restore them.

**If the key leaked:** do steps 1–5 immediately, then treat every database
backup and copy made before the rotation as disclosed to whoever has the old
key, and follow the [security incident runbook](security-incident.md).

## Audit key

New events are signed with the current key; retired keys stay valid for
*verifying* older events.

1. Verify and archive the trail as it stands:

    ```bash
    zentral audit verify
    zentral audit export audit-before-rotation.jsonl
    ```

    Store the export somewhere write-once. Note the last sequence number.

2. Move the current key to the retired list and set a new one:

    ```bash
    ZENTRAL_AUDIT_HMAC_PREVIOUS_KEYS=<old-key>
    ZENTRAL_AUDIT_HMAC_KEY=<new-key>
    ```

3. Restart API and worker; `zentral audit verify` must pass.

!!! warning "After a *leak* of the audit key"
    A retired key still verifies events, so someone holding the leaked key and
    database write access could forge events that pass verification. After a
    leak, the export from step 1 — taken before the attacker could act, stored
    out of their reach — is your trustworthy record of the past. Compare it
    with the database (`diff` against a fresh export) as part of the incident.

## Blind-index key

There is **no rotation tool** for this key. It derives the lookup hashes for
requester identities, message de-duplication and threading; changing it means
existing people are no longer matched to their earlier tickets and privacy
lookups by e-mail stop finding old data.

On its own, a leaked blind-index key reveals nothing. Combined with a copy of
the database it lets the holder test whether a *known* e-mail address has
tickets. If both leaked, handle it as a personal-data incident. Re-keying then
requires a planned data migration — contact the maintainers.

## Webhook ingest tokens

*Administration → Channels → Rotate token*. The old token stops working
immediately; update the sender right away. There is no overlap period — for a
seamless switch, create a second channel, move the sender, delete the first.

## Channel secrets

*Administration → Channels*: replace the secrets of the channel (all fields of
that channel must be re-entered; secrets cannot be read back).

- **E-mail (Graph)**: create a new client secret on the connector's app
  registration, update the channel, then delete the old secret in Entra.
- **Teams**: re-create the outgoing webhook to obtain a new security token.

## Database password

1. Change the password in PostgreSQL (`ALTER ROLE zentral PASSWORD '…'`).
2. Update `ZENTRAL_DATABASE_URL` (and `POSTGRES_PASSWORD` for Compose).
3. Restart API and worker.

## What is *not* a secret in Zentral

Client IDs, tenant IDs, the issuer URL and the public configuration endpoint
are not secrets. The web client is a public OAuth client and has no secret.
