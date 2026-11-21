"""Exception text must go through the same scrubber as every other log value."""

from __future__ import annotations

import logging

import pytest
import structlog

from zentral.logging import configure_logging
from zentral.security.redaction import redact

PRIVATE = [
    "repro@example.com",
    "hunter2-plain",  # password=...
    "hunter2-repr",  # {'password': '...'}, how a dict appears in an error message
    "hunter2 json",  # {"password": "..."}, with a space in it
    "4111 1111 1111 1111",
    "203.0.113.77",
]


def failure() -> RuntimeError:
    request = {"password": "hunter2-repr"}
    message = (
        "failed for repro@example.com from 203.0.113.77 card 4111 1111 1111 1111 "
        f'password=hunter2-plain {request} {{"password": "hunter2 json"}}'
    )
    try:
        try:
            raise ValueError(message)
        except ValueError as cause:
            raise RuntimeError(message) from cause
    except RuntimeError as exc:
        return exc


def through_structlog(exc: RuntimeError) -> None:
    structlog.get_logger("zentral.test").error("unhandled_error", exc_info=exc)


def through_uvicorn(exc: RuntimeError) -> None:
    # How uvicorn reports the exception an application re-raises.
    logging.getLogger("uvicorn.error").error("Exception in ASGI application\n", exc_info=exc)


@pytest.mark.parametrize("emit", [through_structlog, through_uvicorn])
def test_exception_text_is_scrubbed_on_stdout(capsys, emit):
    configure_logging("INFO")
    emit(failure())

    out = capsys.readouterr().out
    assert "RuntimeError" in out and "ValueError" in out, "the error type must stay"
    assert "def failure" in out or "in failure" in out, "the stack frames must stay"
    leaks = [value for value in PRIVATE if value in out]
    assert not leaks, f"private data on stdout: {leaks}"


def test_log_lines_without_an_exception_are_unchanged(capsys):
    configure_logging("INFO")
    logging.getLogger("zentral.test").info("job 2026-11-21 1234567890 done")
    assert "job 2026-11-21 1234567890 done" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{'password': 'a b'}, next", "{'password': '[SECRET]'}, next"),
        ('{"token": "a b"}', '{"token": "[SECRET]"}'),
        ("password=abc def", "password=[SECRET] def"),
        ("my password is abc", "my password is [SECRET]"),
    ],
)
def test_secret_values_are_redacted_however_they_are_quoted(text, expected):
    assert redact(text) == expected
