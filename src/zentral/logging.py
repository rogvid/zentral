"""Structured JSON logging.

Operational logs are for running the system and must not become a shadow copy
of personal data. Rules enforced here and by convention in the code base:

* request bodies, message content, subjects and query strings are never logged;
* a redaction processor scrubs anything that looks like an e-mail address,
  token or other identifier from every string value as a safety net;
* outside development no exception message reaches stdout or stderr, because
  it can quote the request that caused it and no pattern catches a name.
  Tracebacks and crash reports show types and stack frames, Zentral's own log
  lines name the type (:func:`error_fields`), and warnings and errors from
  libraries, which often build their text from an exception, lose their text;
* security-relevant *events* go to the audit trail (and are mirrored here
  under the ``zentral.audit`` logger for SIEM shipping).
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import MutableMapping
from typing import Any

import structlog

from zentral.security.redaction import redact

_SAFE_KEYS = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "logger",
        "request_id",
        "path",
        "method",
        "action",
        "resource_id",
        "resource_type",
        "actor_id",
        "job_id",
        "kind",
        "channel",
        "outcome",
    }
)

_CAUSE = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT = "\nDuring handling of the above exception, another exception occurred:\n\n"


def _scrub(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in event.items():
        if isinstance(value, str) and key not in _SAFE_KEYS:
            event[key] = redact(value)
    return event


def _type_name(exc: BaseException) -> str:
    kind = type(exc)
    return (
        kind.__qualname__
        if kind.__module__ == "builtins"
        else f"{kind.__module__}.{kind.__qualname__}"
    )


def _lines_without_messages(exc: BaseException, seen: set[int]) -> list[str]:
    """``traceback.format_exception`` for a chain, keeping only types and frames."""
    lines: list[str] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        block = [f"{_type_name(current)}\n"]
        if current.__traceback__ is not None:
            frames = traceback.format_tb(current.__traceback__)
            block = ["Traceback (most recent call last):\n", *frames, *block]
        if isinstance(current, BaseExceptionGroup):
            for member in current.exceptions:
                block.extend(_lines_without_messages(member, seen))
        lines = block + lines
        chained, link = current.__cause__, _CAUSE
        if chained is None and not current.__suppress_context__:
            chained, link = current.__context__, _CONTEXT
        if chained is not None and id(chained) not in seen:
            lines.insert(0, link)
        current = chained
    return lines


def _format_without_messages(exc_info: Any) -> str:
    exc = exc_info[1]
    return "".join(_lines_without_messages(exc, set())).rstrip("\n") if exc else "NoneType"


class _RedactingFormatter(logging.Formatter):
    """Scrubs tracebacks only: other lines are structured and scrubbed already."""

    def formatException(self, ei: Any) -> str:
        return redact(super().formatException(ei))

    def formatStack(self, stack_info: str) -> str:
        return redact(super().formatStack(stack_info))


def _without_exception(value: Any) -> Any:
    return _type_name(value) if isinstance(value, BaseException) else value


class _MessageFreeFormatter(_RedactingFormatter):
    def format(self, record: logging.LogRecord) -> str:
        safe = logging.makeLogRecord(record.__dict__)
        safe.exc_text = None  # another handler's rendering would still hold the message
        if record.name.partition(".")[0] != "zentral" and record.levelno >= logging.WARNING:
            safe.msg, safe.args = f"{record.name} {record.levelname}: [message not logged]", None
        else:
            safe.msg = _without_exception(record.msg)
            if isinstance(record.args, tuple):
                safe.args = tuple(_without_exception(arg) for arg in record.args)
            elif record.args:
                safe.args = {k: _without_exception(v) for k, v in record.args.items()}
        return super().format(safe)

    def formatException(self, ei: Any) -> str:
        return redact(_format_without_messages(ei))


def _json_without_exceptions(value: Any) -> str:
    return type(value).__name__ if isinstance(value, BaseException) else repr(value)


def error_fields(exc: BaseException) -> dict[str, str]:
    """Log fields describing a failure by its types, never its text."""
    fields = {"error": type(exc).__name__}
    if exc.__cause__ is not None:
        fields["cause"] = type(exc.__cause__).__name__
    return fields


def _development() -> bool:
    """Asked at crash time; a configuration that cannot be read counts as production."""
    try:
        from zentral.config import get_settings

        return get_settings().env == "development"
    except Exception:
        return False


def _write_crash(exc_info: Any) -> None:
    sys.stderr.write(f"{redact(_format_without_messages(exc_info))}\n")


def _report_crash(kind: Any, value: Any, tb: Any) -> None:
    if _development():
        sys.__excepthook__(kind, value, tb)
    else:
        _write_crash((kind, value, tb))


def _report_thread_crash(args: Any) -> None:
    if _development():
        threading.__excepthook__(args)
    elif args.exc_type is not SystemExit:  # a thread's normal way out
        _write_crash((args.exc_type, args.exc_value, args.exc_traceback))


def _report_unraisable(args: Any) -> None:
    if _development():
        sys.__unraisablehook__(args)
    else:
        _write_crash((args.exc_type, args.exc_value, args.exc_traceback))


def install_crash_hooks() -> None:
    """Keep messages out of what Python prints for an exception nobody caught."""
    sys.excepthook = _report_crash
    threading.excepthook = _report_thread_crash
    sys.unraisablehook = _report_unraisable


def configure_logging(level: str = "INFO", *, exception_messages: bool = False) -> None:
    """Set up stdout logging; ``exception_messages`` is for development only."""
    formatter = _RedactingFormatter if exception_messages else _MessageFreeFormatter
    render_exception = (
        structlog.processors.format_exc_info
        if exception_messages
        else structlog.processors.ExceptionRenderer(_format_without_messages)
    )
    logging.basicConfig(stream=sys.stdout, level=level.upper(), format="%(message)s", force=True)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter("%(message)s"))
    # Third-party request logging can include URLs with identifiers.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            render_exception,  # before _scrub, which then covers its text
            _scrub,
            structlog.processors.JSONRenderer(
                **({} if exception_messages else {"default": _json_without_exceptions})
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
