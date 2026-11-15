"""Custom column types."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from zentral.security.crypto import get_cipher


class EncryptedText(TypeDecorator[str]):
    """Text that is encrypted by the application before it reaches the database."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().decrypt(value)


class EncryptedJSON(TypeDecorator[dict[str, Any]]):
    """A JSON object stored as a single encrypted blob (connector secrets)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: dict[str, Any] | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().encrypt(json.dumps(value, sort_keys=True))

    def process_result_value(self, value: str | None, dialect: Dialect) -> dict[str, Any] | None:
        if value is None:
            return None
        loaded: dict[str, Any] = json.loads(get_cipher().decrypt(value))
        return loaded


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
