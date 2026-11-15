"""Structured JSON logging.

Operational logs are for running the system and must not become a shadow copy
of personal data. Rules enforced here and by convention in the code base:

* request bodies, message content, subjects and query strings are never logged;
* a redaction processor scrubs anything that looks like an e-mail address,
  token or other identifier from every string value as a safety net;
* security-relevant *events* go to the audit trail (and are mirrored here
  under the ``zentral.audit`` logger for SIEM shipping).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from zentral.security.redaction import redact

_SAFE_KEYS = frozenset(
    {"event", "level", "timestamp", "logger", "request_id", "path", "method", "action",
     "resource_id", "resource_type", "actor_id", "job_id", "kind", "channel", "outcome"}
)


def _scrub(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in event.items():
        if isinstance(value, str) and key not in _SAFE_KEYS:
            event[key] = redact(value)
    return event


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level.upper(), format="%(message)s", force=True)
    # Third-party request logging can include URLs with identifiers.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _scrub,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
