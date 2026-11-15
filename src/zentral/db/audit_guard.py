"""Database-level protection of the audit table (PostgreSQL).

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
