"""Domain errors. The API and MCP layers translate these to protocol responses."""

from __future__ import annotations


class DomainError(Exception):
    pass


class PermissionDenied(DomainError):
    def __init__(self, permission: str, resource_type: str = "", resource_id: str = "") -> None:
        super().__init__(f"missing permission {permission}")
        self.permission = permission
        self.resource_type = resource_type
        self.resource_id = resource_id


class NotFound(DomainError):
    def __init__(self, what: str = "resource") -> None:
        super().__init__(f"{what} not found")


class ValidationFailed(DomainError):
    pass


class Conflict(DomainError):
    pass
