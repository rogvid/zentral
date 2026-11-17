"""Role-based access control.

Roles are *Entra ID app roles* delivered in the ``roles`` claim of the access
token; Zentral never stores role assignments itself, so joiners/movers/leavers
are handled entirely in the identity provider.

The role → permission mapping below is the single source of truth. It is
deny-by-default: a principal has exactly the union of the permissions of its
roles and nothing else. Unknown roles grant nothing.
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    TICKET_READ = "ticket:read"
    # See requester identity and unredacted content.
    TICKET_READ_PII = "ticket:read_pii"
    # See tickets of every team, not only one's own.
    TICKET_READ_ALL = "ticket:read_all"
    # See tickets that are not yet assigned to a team.
    TICKET_TRIAGE = "ticket:triage"
    TICKET_CREATE = "ticket:create"
    TICKET_UPDATE = "ticket:update"
    TICKET_ASSIGN = "ticket:assign"
    # Send a message to the requester (leaves the organisation).
    TICKET_REPLY = "ticket:reply"
    TICKET_NOTE = "ticket:note"
    TEAM_MANAGE = "team:manage"
    SYSTEM_MANAGE = "system:manage"
    SOURCE_MANAGE = "source:manage"
    RULE_MANAGE = "rule:manage"
    # Maintain the saved replies agents start from.
    TEMPLATE_MANAGE = "template:manage"
    AUDIT_READ = "audit:read"
    PRIVACY_EXPORT = "privacy:export"
    PRIVACY_ERASE = "privacy:erase"
    # Record or withdraw a person's objection to automated (AI) processing.
    PRIVACY_RESTRICT = "privacy:restrict"
    INGEST_WRITE = "ingest:write"


class Role(StrEnum):
    ADMIN = "Zentral.Admin"
    AGENT = "Zentral.Agent"
    VIEWER = "Zentral.Viewer"
    AUDITOR = "Zentral.Auditor"
    PRIVACY_OFFICER = "Zentral.PrivacyOfficer"
    # Least-privilege role intended for AI agents connecting over MCP.
    AUTOMATION = "Zentral.Automation"
    # Services that may only submit new issues.
    INGEST = "Zentral.Ingest"


P = Permission

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    # Administrators configure the system. They deliberately do NOT get the
    # GDPR export/erase permissions (segregation of duties).
    Role.ADMIN: frozenset(
        {
            P.TICKET_READ,
            P.TICKET_READ_PII,
            P.TICKET_READ_ALL,
            P.TICKET_TRIAGE,
            P.TICKET_CREATE,
            P.TICKET_UPDATE,
            P.TICKET_ASSIGN,
            P.TICKET_REPLY,
            P.TICKET_NOTE,
            P.TEAM_MANAGE,
            P.SYSTEM_MANAGE,
            P.SOURCE_MANAGE,
            P.RULE_MANAGE,
            P.TEMPLATE_MANAGE,
            P.AUDIT_READ,
        }
    ),
    Role.AGENT: frozenset(
        {
            P.TICKET_READ,
            P.TICKET_READ_PII,
            P.TICKET_TRIAGE,
            P.TICKET_CREATE,
            P.TICKET_UPDATE,
            P.TICKET_ASSIGN,
            P.TICKET_REPLY,
            P.TICKET_NOTE,
        }
    ),
    Role.VIEWER: frozenset({P.TICKET_READ}),
    # Auditors see the audit trail but no ticket content.
    Role.AUDITOR: frozenset({P.AUDIT_READ}),
    Role.PRIVACY_OFFICER: frozenset(
        {
            P.TICKET_READ,
            P.TICKET_READ_PII,
            P.TICKET_READ_ALL,
            P.TICKET_TRIAGE,
            P.PRIVACY_EXPORT,
            P.PRIVACY_ERASE,
            P.PRIVACY_RESTRICT,
            P.AUDIT_READ,
        }
    ),
    # Reads redacted content, may triage and annotate. Cannot see PII and
    # cannot send anything to a requester.
    Role.AUTOMATION: frozenset(
        {P.TICKET_READ, P.TICKET_TRIAGE, P.TICKET_UPDATE, P.TICKET_NOTE, P.TICKET_CREATE}
    ),
    Role.INGEST: frozenset({P.INGEST_WRITE}),
}


def permissions_for(roles: frozenset[str]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for name in roles:
        try:
            granted |= ROLE_PERMISSIONS[Role(name)]
        except ValueError:
            continue  # unknown role: grants nothing
    return frozenset(granted)
