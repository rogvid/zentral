# First deployment

**Goal:** a production installation that passes `zentral doctor`, with roles
assigned and one channel delivering tickets.
**Time:** 1–2 hours. **Who:** platform operator + identity administrator +
the future Zentral administrator.

## Before you start

- [ ] Decide the public hostname and obtain DNS for it
- [ ] Decide where it runs: [Compose](../operations/deploy-compose.md) or
      [Kubernetes](../operations/deploy-kubernetes.md)
- [ ] PostgreSQL 14+ available (or use the Compose-provided one)
- [ ] A key-encryption key in Azure Key Vault / Managed HSM or Vault Transit
      ([Key custody](../operations/key-custody.md)), and a safe for the
      offline escrow copy
- [ ] Names of the first administrators, auditor and privacy officer
- [ ] Your retention period (days) from your data-protection policy

## Platform

1. **Identity.** Create the app registrations/clients and roles:
   [Entra ID](../operations/identity-entra.md) or
   [other OIDC provider](../operations/identity-oidc.md). Assign yourself
   `Zentral.Admin`.

2. **Configuration and keys.** Set up the key provider and create the keyring
   (`zentral keys init`) as described in
   [Key custody](../operations/key-custody.md#new-installation), then fill in
   the configuration as described in your platform guide.

    !!! danger "Make the escrow copy now"
        Write the [offline escrow keyring](../operations/key-custody.md#escrow-and-disaster-recovery)
        and turn on purge protection for the KMS key **before** any data
        exists. Without the keys, the database is unreadable — permanently.

3. **Start** the stack (platform guide). Migrations run automatically
   (Compose) or as the `zentral-migrate` Job (Kubernetes).

4. **Pre-flight.**

    ```bash
    zentral doctor
    ```

    Expect every line `✓`. A `!` on *AI provider* is fine if you start with
    `rules`.

5. **TLS and headers.** From outside:

    ```bash
    curl -sI https://servicedesk.example.com/healthz | grep -iE 'strict-transport|content-security|x-frame'
    curl -s  https://servicedesk.example.com/readyz
    curl -s -o /dev/null -w '%{http_code}\n' https://servicedesk.example.com/api/v1/tickets   # 401
    curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example' https://servicedesk.example.com/healthz   # 400
    ```

## In the application

6. **Sign in** as administrator. If you see "Your role does not include
   access", the role assignment has not reached your token yet — sign out and
   in again.

7. **Teams.** *Administration → Teams*: create your queues. Ask the first
   agents to sign in once (they appear in the member list after that), then add
   them — or link identity-provider groups.

8. **Systems.** *Administration → Systems*: add the handful of systems that
   generate most of your tickets, each with an owning team and a few keywords
   ([guide](../guides/systems.md#starting-out)).

9. **A first channel.** Start with a [webhook](../guides/connectors/webhook.md)
   — it needs nothing external — and send a test:

    ```bash
    curl -X POST "$HOOK_URL" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"title":"Deployment test","description":"Hello from the first deployment.","severity":"low"}'
    ```

    The ticket appears in the inbox; within seconds it gets an AI category.
    Then add [e-mail](../guides/connectors/email.md) and/or
    [Teams](../guides/connectors/teams.md).

10. **Rules.** Add one or two automation rules
    ([examples](../guides/administration.md#automation-rules)).

## Verify compliance functions

11. As **auditor**: *Audit trail → Verify integrity* → "Chain intact". Find
    your own actions from the steps above.
12. As **privacy officer**: create a test ticket with a requester address,
    look the address up, export, erase. Confirm the ticket shows `[erased]`.
13. As **agent** in one team: confirm you cannot open a ticket of another
    team (the link returns "not found").

## Operations hand-over

- [ ] Backups scheduled and **one restore tested**
      ([runbook](backup-restore.md))
- [ ] Logs shipped; alerts configured ([Monitoring](../operations/monitoring.md))
- [ ] Daily `zentral audit verify` scheduled
- [ ] Calendar reminders: channel secret expiry, certificate expiry, quarterly
      access review
- [ ] On-call knows where the [runbooks](index.md) are
- [ ] Plan for [on-prem AI](switch-to-on-prem-ai.md) if you started with `rules`

## Before real personal data goes in

- [ ] DPIA completed; Zentral added to your record of processing activities
- [ ] Retention period set deliberately (`ZENTRAL_RETENTION_DAYS`)
- [ ] AI provider is `rules` or an on-prem model
- [ ] Staff briefed ([agent guide](../guides/agents.md#good-practice-with-personal-data))
- [ ] The limitations in [Security posture](../compliance/security-posture.md)
      reviewed and accepted by the risk owner

## Back out

Nothing outside Zentral is modified by an installation. Stop the stack, drop
the database, delete the app registrations.
