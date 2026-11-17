"""Database-level protection of the audit tables (PostgreSQL).

Defence in depth on top of the hash chain: once installed, not even the
application's own database role can UPDATE, DELETE or TRUNCATE audit events.
Removing the guard requires table-owner rights and is itself visible in the
database's own logs.
"""

from __future__ import annotations

INSTALL: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION zentral_audit_immutable() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION zentral_audit_immutable()
    """,
    """
    CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION zentral_audit_immutable()
    """,
)

REMOVE: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events",
    "DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events",
    "DROP FUNCTION IF EXISTS zentral_audit_immutable()",
)


# Revision 0008: archived events may be removed, but only
#   * inside a transaction that declared it (SET LOCAL zentral.audit_prune = <seq>),
#   * up to that sequence number, and
#   * up to a checkpoint that already exists (written earlier in the same transaction).
# Everything else — any UPDATE, any other DELETE, TRUNCATE — is still refused.
# Checkpoints themselves can never be changed or removed.
PRUNE_AWARE: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION zentral_audit_immutable() RETURNS trigger AS $$
    DECLARE
        allowed text := current_setting('zentral.audit_prune', true);
    BEGIN
        IF TG_OP = 'DELETE' AND allowed IS NOT NULL AND allowed <> ''
           AND OLD.seq <= allowed::bigint
           AND OLD.seq <= (SELECT coalesce(max(seq), 0) FROM audit_checkpoints) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'audit_events is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION zentral_audit_checkpoint_immutable() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_checkpoints is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER audit_checkpoints_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_checkpoints
    FOR EACH ROW EXECUTE FUNCTION zentral_audit_checkpoint_immutable()
    """,
    """
    CREATE TRIGGER audit_checkpoints_no_truncate
    BEFORE TRUNCATE ON audit_checkpoints
    FOR EACH STATEMENT EXECUTE FUNCTION zentral_audit_checkpoint_immutable()
    """,
)

PRUNE_AWARE_REMOVE: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS audit_checkpoints_no_truncate ON audit_checkpoints",
    "DROP TRIGGER IF EXISTS audit_checkpoints_no_update_delete ON audit_checkpoints",
    "DROP FUNCTION IF EXISTS zentral_audit_checkpoint_immutable()",
    # Back to the original, unconditional guard.
    INSTALL[0],
)
