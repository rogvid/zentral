"""Error monitoring with Sentry, off unless ``ZENTRAL_SENTRY_DSN`` is set.

A service desk holds personal data in nearly every value it handles, so only
what describes the *code* leaves the process: exception types, stack frames
without variables, route templates, status codes, timings and the release.

The SDK's options alone do not guarantee that, so this module goes further than
they do:

* exception messages are dropped: a database error quotes its parameters;
* the request keeps its method and nothing else: paths hold ticket ids, query
  strings hold file names and search terms, headers hold tokens and addresses;
* no breadcrumbs and no log capture: log lines are for the host's own logs;
* no auto-enabled integrations: the database, HTTP client, MCP and AI ones
  would record statements, webhook URLs, tool arguments and prompts.
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from zentral.config import Settings

# Polled by the container health check: tracing them would only burn quota.
_UNTRACED_PATHS = frozenset({"/healthz", "/readyz"})

_REQUEST_KEPT = frozenset({"method"})
_EVENT_DROPPED = ("breadcrumbs", "logentry", "message", "extra", "server_name")

# Every category is named: the SDK's default for one left out is to collect it.
_OFF: dict[str, Any] = {"mode": "off"}
_DATA_COLLECTION: dict[str, Any] = {
    "user_info": False,
    "cookies": _OFF,
    "http_headers": {"request": _OFF},
    "http_bodies": [],
    "url_query_params": _OFF,
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
}


def _scrub_event(event: Any, _hint: Any) -> Any:
    """The last check on every error and transaction before it leaves."""
    for key in _EVENT_DROPPED:
        event.pop(key, None)
    # Sentry fills in a city from the sender's address unless the event already
    # has a location, whatever the IP settings say. An empty one stores none.
    event["user"] = {"geo": {}}
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {k: v for k, v in request.items() if k in _REQUEST_KEPT}
    exceptions = event.get("exception")
    for entry in exceptions.get("values", []) if isinstance(exceptions, dict) else []:
        entry.pop("value", None)
    for span in event.get("spans") or []:
        if isinstance(span, dict):
            span.pop("data", None)
    return event


def _traces_sampler(rate: float) -> Any:
    def sampler(context: dict[str, Any]) -> float:
        scope = context.get("asgi_scope") or {}
        if scope.get("path") in _UNTRACED_PATHS:
            return 0.0
        parent = context.get("parent_sampled")
        return float(parent) if parent is not None else rate

    return sampler


def sentry_options(settings: Settings) -> dict[str, Any]:
    """Everything but the DSN, so that the privacy test runs the real setup."""
    return {
        "environment": settings.env,
        "release": settings.sentry_release or None,
        "data_collection": _DATA_COLLECTION,
        "max_breadcrumbs": 0,
        "auto_enabling_integrations": False,
        "integrations": [
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            LoggingIntegration(level=None, event_level=None),
        ],
        "before_send": _scrub_event,
        "before_send_transaction": _scrub_event,
        "traces_sampler": _traces_sampler(settings.sentry_traces_sample_rate),
    }


def init_sentry(settings: Settings) -> None:
    """Start the SDK; does nothing without a DSN."""
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, **sentry_options(settings))


def capture_exception(exc: BaseException) -> None:
    """Report a handled failure; does nothing while the SDK is off."""
    sentry_sdk.capture_exception(exc)
