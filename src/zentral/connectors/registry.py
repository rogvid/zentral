"""Connector discovery.

Connectors are discovered through the ``zentral.connectors`` entry-point group,
so third-party packages can add channels without modifying Zentral. Because
loading an entry point executes its code, only names listed in
``ZENTRAL_ENABLED_CONNECTORS`` are ever imported (explicit allow-list).
"""

from __future__ import annotations

from importlib.metadata import entry_points

from zentral.config import Settings
from zentral.connectors.base import Connector

GROUP = "zentral.connectors"


class ConnectorRegistry:
    def __init__(self, connectors: dict[str, type[Connector]]) -> None:
        self._connectors = connectors

    @classmethod
    def load(cls, settings: Settings) -> ConnectorRegistry:
        found: dict[str, type[Connector]] = {}
        for ep in entry_points(group=GROUP):
            if ep.name not in settings.enabled_connectors:
                continue
            klass = ep.load()
            if not (isinstance(klass, type) and issubclass(klass, Connector)):
                raise TypeError(f"entry point {ep.name!r} is not a Connector subclass")
            if klass.name != ep.name:
                raise TypeError(f"connector {klass.name!r} registered under {ep.name!r}")
            found[ep.name] = klass
        return cls(found)

    def get(self, name: str) -> type[Connector] | None:
        return self._connectors.get(name)

    def names(self) -> list[str]:
        return sorted(self._connectors)

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "capabilities": sorted(c.value for c in klass.capabilities),
                "config_schema": klass.config_model.model_json_schema(),
                "secret_fields": sorted(klass.secret_fields),
                "required_secrets": sorted(klass.required_secrets),
                "uses_ingest_token": klass.uses_ingest_token,
            }
            for name, klass in sorted(self._connectors.items())
        ]
