# Runbooks

Step-by-step procedures for the people who operate Zentral. Each one states
when to use it, what you need, the steps, how to verify, and how to back out.

| Situation | Runbook |
|---|---|
| Setting up a new installation | [First deployment](first-deployment.md) |
| A new version is available | [Upgrade](upgrade.md) |
| Setting up or testing backups; disaster recovery | [Backup and restore](backup-restore.md) |
| Scheduled rotation, staff leaving, or a suspected leak | [Rotate keys and secrets](key-rotation.md) |
| Routine integrity check; archiving; "tampering detected" | [Verify the audit trail](audit-verification.md) |
| Suspected breach or misuse | [Security incident](security-incident.md) |
| Someone asks for their data, or for deletion | [Data-subject request](data-subject-request.md) |
| Moving AI triage onto your own hardware | [Switch to on-prem AI](switch-to-on-prem-ai.md) |
| Quarterly review of who can do what | [Access review](access-review.md) |
| Something is not working | [Troubleshooting](troubleshooting.md) |

## Conventions

Commands are written for the bare CLI. Prefix them for your platform:

| Platform | Prefix |
|---|---|
| Docker Compose | `docker compose run --rm api …` (e.g. `docker compose run --rm api doctor`) |
| Kubernetes | `kubectl -n zentral exec deploy/zentral-api -- zentral …` |
| Local | `uv run zentral …` |

`psql` examples assume a connection to the Zentral database with a suitably
privileged role.

!!! tip "Rehearse"
    Run the restore, key-rotation and incident runbooks once on a test
    installation before you need them for real. A runbook nobody has practised
    is a hypothesis.
