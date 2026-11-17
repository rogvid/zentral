"""The authenticated caller."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from zentral.security.rbac import Permission, permissions_for

PrincipalKind = Literal["user", "service", "system", "requester"]


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str  # Entra object id (oid); stable, non-reassignable
    tenant_id: str
    kind: PrincipalKind
    roles: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    display_name: str = ""
    email: str = ""
    client_app_id: str = ""  # azp/appid: which application obtained the token
    permissions: frozenset[Permission] = field(default=frozenset())

    @classmethod
    def build(
        cls,
        *,
        subject: str,
        tenant_id: str,
        kind: PrincipalKind,
        roles: frozenset[str],
        groups: frozenset[str] = frozenset(),
        display_name: str = "",
        email: str = "",
        client_app_id: str = "",
    ) -> Principal:
        return cls(
            subject=subject,
            tenant_id=tenant_id,
            kind=kind,
            roles=roles,
            groups=groups,
            display_name=display_name,
            email=email,
            client_app_id=client_app_id,
            permissions=permissions_for(roles),
        )

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


def system_principal(component: str) -> Principal:
    """Internal actor for background work (worker, CLI). Never reachable over the network."""
    return Principal(
        subject=f"system:{component}",
        tenant_id="",
        kind="system",
        roles=frozenset({"system"}),
        display_name=f"Zentral {component}",
        permissions=frozenset(Permission),
    )


def requester_principal(contact_id: object) -> Principal:
    """A requester signed in to the portal. Holds no staff permission at all:
    portal operations check ownership of each ticket themselves."""
    return Principal(
        subject=f"contact:{contact_id}",
        tenant_id="",
        kind="requester",
        roles=frozenset(),
        display_name="Requester",
    )
