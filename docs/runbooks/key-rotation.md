# Rotate keys and secrets

| Secret | Rotate routinely | Rotate immediately when |
|---|---|---|
| [Key-encryption key (KMS / HSM)](#key-encryption-key) | Yearly, or per your KMS policy | Policy demands it; the KMS reports a problem |
| [Encryption key](#encryption-key) | Yearly | It may have leaked; an operator with access leaves |
| [Audit key](#audit-key) | Yearly | It may have leaked |
| [Blind-index key](#blind-index-key) | No (see below) | It leaked *together with* a database copy |
| [Webhook ingest tokens](#webhook-ingest-tokens) | Yearly | Leaked; sender decommissioned |
| [Channel secrets](#channel-secrets) (Graph, Teams) | Before expiry | Leaked |
| [Database password](#database-password) | Per your policy | Leaked |

Take a backup first. Run `zentral doctor` before and after every rotation.

Each key section has two procedures: **with a keyring** (production, see
[Key custody](../operations/key-custody.md)) and **with plain-text keys**
(`ZENTRAL_KEY_PROVIDER=env`). Run the `zentral keys` commands in an
administrator shell with wrap permission and a writable copy of the keyring;
each writes `keyring.json` in place (keeping a dated `.bak`) — deliver the new
file to the deployment before restarting. After any change of the keys
(everything except `rewrap`), refresh the
[escrow copy](../operations/key-custody.md#escrow-and-disaster-recovery).

## Key-encryption key

Only the envelope changes; no data is re-encrypted and nothing is unreadable
at any point.

1. Create a new version of the key in the KMS (`az keyvault key rotate …`, or
   `vault write -f transit/keys/zentral/rotate`). The old version stays
   enabled, so running instances and the current keyring keep working.
2. Re-wrap the keyring under the newest version:

    ```bash
    zentral keys rewrap
    # wrote keyring.json (azure_keyvault, KEK https://desk-kv.vault.azure.net/keys/zentral-kek/9c1e…)
    ```

3. Deliver it, restart API and worker, `zentral doctor`.
4. Re-wrap every `.bak` keyring you still keep for old backups
   (`ZENTRAL_KEYRING_FILE=keyring.json.<timestamp>.bak zentral keys rewrap`),
   and the [escrow copy](../operations/key-custody.md#escrow-and-disaster-recovery)
   is unaffected. Then disable the old KMS key version (Vault: raise
   `min_decryption_version`).

## Encryption key

Zero downtime. The **first** key encrypts, **all** keys decrypt.

### With a keyring

1. `zentral keys add-data-key` — adds a new first key.
2. Deliver the keyring; restart API and worker. New data is written with the
   new key; old data is still readable.
3. `zentral keys rotate` — re-encrypts existing data.
4. `zentral keys retire-data-keys` — checks that every stored value opens with
   the new key (refuses otherwise), then drops the old one.
5. Deliver, restart, `zentral doctor` (`✓ encryption: 1 key(s) configured`).
6. Keep the `.bak` keyring from before step 4 until every database backup
   taken before step 3 has expired — you need it to restore them.

### With plain-text keys

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

With a keyring, step 2 below is `zentral keys rotate-audit-key` (the current
key moves to the retired list, a new one is generated) followed by delivering
the keyring.

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
