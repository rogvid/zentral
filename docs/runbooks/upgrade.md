# Upgrade

**When:** a new Zentral version is released, or you rebuilt the image for
dependency updates. **Downtime:** none for patch releases with ≥ 2 API
replicas; a few seconds on a single-host Compose installation.

## Before

1. Read the release notes for migration or configuration changes.
2. Take a database backup ([runbook](backup-restore.md)) and note the current
   schema revision:

    ```bash
    zentral db current
    ```

3. Confirm the installation is healthy *before* changing it: `zentral doctor`.

!!! warning "Upgrading to the release with key custody"
    From this release, production refuses plain-text keys in the environment.
    Before upgrading, either [move to a keyring](../operations/key-custody.md#move-an-existing-installation)
    (recommended — do it on the *new* version's CLI, then deploy), or set
    `ZENTRAL_ALLOW_ENV_KEYS=true` to keep the old behaviour knowingly
    (`zentral doctor` will warn).

## Upgrade

=== "Docker Compose"

    ```bash
    cd zentral && git pull            # or change ZENTRAL_IMAGE in .env to the new tag
    docker build -t zentral:local .
    cd deploy/compose
    docker compose up -d              # runs migrate first, then recreates api and worker
    docker compose run --rm api doctor
    ```

=== "Kubernetes"

    ```bash
    # set the new image tag in your overlay, then:
    kubectl -n zentral delete job zentral-migrate --ignore-not-found
    kubectl apply -k deploy/kubernetes/overlays/prod
    kubectl -n zentral wait --for=condition=complete job/zentral-migrate --timeout=300s
    kubectl -n zentral rollout status deploy/zentral-api
    kubectl -n zentral rollout status deploy/zentral-worker
    kubectl -n zentral exec deploy/zentral-api -- zentral doctor
    ```

    New API pods report *not ready* until the schema matches their version, so
    traffic stays on the old pods until the migration has finished.

## Verify

- [ ] `zentral doctor`: all `✓`, schema at the new revision
- [ ] Sign in; open a ticket; add a note
- [ ] Send a test message through one channel; it is classified
- [ ] `zentral audit verify` passes
- [ ] No `status>=500` in the access log

## Roll back

Migrations are written to be backward compatible within a minor version (they
add; they do not remove). So for a **patch or minor** release:

1. Redeploy the previous image tag. Leave the schema as it is.

If the previous version cannot run on the new schema (release notes will say
so), or data was damaged:

2. Stop API and worker.
3. Restore the pre-upgrade backup ([runbook](backup-restore.md)).
4. Start the previous version.

Tickets received between backup and restore are lost from Zentral; e-mail is
re-ingested automatically from the mailbox (the polling cursor is restored
too), webhook and Teams messages are not.

## Dependency-only rebuilds

Rebuild the image at least monthly to pick up base-image and dependency
patches, even without a new Zentral version. Dependabot opens the pull
requests; CI runs the full suite, `pip-audit` and `npm audit` on each.
