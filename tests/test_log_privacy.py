"""Exception messages stay off stdout, and any text kept is scrubbed like other values."""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import subprocess
import sys
import traceback
import uuid
from pathlib import Path

import httpx
import pytest
import structlog

import zentral
from tests.test_ai import classified, use_fake
from zentral.ai.base import AIError
from zentral.api import deps
from zentral.api.app import create_app
from zentral.connectors.base import Capability, ConnectorError, PollResult
from zentral.db.models import IngestSource, Job
from zentral.logging import configure_logging
from zentral.mcp_server import _Verifier
from zentral.security.redaction import redact
from zentral.security.tokens import AuthenticationError
from zentral.services import jobs

NAME = "Margit Joensen"  # matches no redaction pattern
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


def chained_failure() -> RuntimeError:
    """Three errors quoting a name, linked by ``__context__`` and ``__cause__``."""
    try:
        try:
            try:
                raise KeyError(f"no record for {NAME}")
            except KeyError:
                raise ValueError(f"could not load {NAME}")  # noqa: B904
        except ValueError as cause:
            raise RuntimeError(f"failed for {NAME}") from cause
    except RuntimeError as exc:
        return exc


@pytest.mark.parametrize("emit", [through_structlog, through_uvicorn])
def test_exception_text_is_scrubbed_on_stdout(capsys, emit):
    configure_logging("INFO", exception_messages=True)
    emit(failure())

    out = capsys.readouterr().out
    assert "RuntimeError" in out and "ValueError" in out, "the error type must stay"
    assert "def failure" in out or "in failure" in out, "the stack frames must stay"
    leaks = [value for value in PRIVATE if value in out]
    assert not leaks, f"private data on stdout: {leaks}"


@pytest.mark.parametrize("emit", [through_structlog, through_uvicorn])
def test_exception_messages_stay_off_stdout_by_default(capsys, emit):
    configure_logging("INFO")
    emit(chained_failure())

    out = capsys.readouterr().out
    assert NAME not in out
    for kept in ("KeyError", "ValueError", "RuntimeError", "in chained_failure"):
        assert kept in out, f"{kept} must stay"
    assert "direct cause" in out and "During handling" in out, "the chain must stay readable"


@pytest.mark.parametrize("emit", [through_structlog, through_uvicorn])
def test_exception_group_members_lose_their_messages(capsys, emit):
    configure_logging("INFO")
    try:
        raise ExceptionGroup(f"group for {NAME}", [ValueError(f"member for {NAME}")])
    except ExceptionGroup as exc:
        emit(exc)

    out = capsys.readouterr().out
    assert NAME not in out
    assert "ExceptionGroup" in out and "ValueError" in out


def test_suppressed_context_stays_hidden(capsys):
    configure_logging("INFO")
    try:
        try:
            raise KeyError("inner")
        except KeyError:
            raise RuntimeError("outer") from None
    except RuntimeError as exc:
        through_uvicorn(exc)

    out = capsys.readouterr().out
    assert "RuntimeError" in out and "KeyError" not in out


def test_a_chain_that_loops_still_logs(capsys):
    configure_logging("INFO")
    first, second = ValueError(NAME), RuntimeError(NAME)
    first.__cause__, second.__cause__ = second, first
    through_uvicorn(first)

    out = capsys.readouterr().out
    assert NAME not in out
    assert "ValueError" in out and "RuntimeError" in out


@pytest.mark.parametrize("emit", [through_structlog, through_uvicorn])
def test_development_can_keep_exception_messages(capsys, emit):
    configure_logging("INFO", exception_messages=True)
    emit(chained_failure())
    assert NAME in capsys.readouterr().out


# --- Zentral's own log lines ------------------------------------------------------------------


class Refuses:
    async def validate(self, _token: str) -> None:
        try:
            raise ValueError(f"bad claim for {NAME}")
        except ValueError as exc:
            raise AuthenticationError(f"refused {NAME}") from exc


def fresh_logs(monkeypatch, *modules) -> None:
    """Module loggers cache the first configuration: give them the production one."""
    configure_logging("INFO")
    for module in modules:
        monkeypatch.setattr(module, "log", structlog.get_logger(module.__name__))


def assert_only_types(out: str, *kept: str) -> None:
    assert NAME not in out
    for value in kept:
        assert value in out, f"{value} must stay"


async def test_failed_sign_in_logs_the_type(runtime, monkeypatch, capsys):
    app = create_app(runtime.settings, runtime=runtime, validator=Refuses())
    fresh_logs(monkeypatch, deps)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/me", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401
    assert_only_types(capsys.readouterr().out, "authentication_failed", "AuthenticationError")


async def test_failed_mcp_sign_in_logs_the_type(monkeypatch, capsys):
    import zentral.mcp_server as mcp_server

    fresh_logs(monkeypatch, mcp_server)
    assert await _Verifier(Refuses()).verify_token("x") is None
    assert_only_types(
        capsys.readouterr().out, "mcp_authentication_failed", "AuthenticationError", "ValueError"
    )


async def test_failed_ai_provider_logs_the_type(client, auth, worker, runtime, monkeypatch, capsys):
    import zentral.ai.service as service

    use_fake(runtime, error=AIError(f"model refused {NAME}"))
    fresh_logs(monkeypatch, service)
    await classified(client, auth, worker, subject="VPN down", body="vpn cannot connect")
    assert_only_types(capsys.readouterr().out, "ai_provider_failed", "AIError")


async def test_failed_job_logs_the_type_and_keeps_its_stored_error(
    worker, runtime, monkeypatch, capsys
):
    async def fails(_ctx, _payload) -> None:
        raise ConnectorError(f"could not deliver to {NAME}")

    worker._handlers["probe"] = fails
    async with runtime.sessions() as session:
        job = Job(kind="probe", payload={})
        session.add(job)
        await session.commit()
        job_id = job.id
    fresh_logs(monkeypatch, jobs)
    await worker.run_once()

    assert_only_types(capsys.readouterr().out, "job_failed", "ConnectorError")
    async with runtime.sessions() as session:
        stored = await session.get(Job, job_id)
    assert NAME in stored.last_error, "what the database stores is not part of this rule"


async def test_failed_acknowledgement_logs_the_type(worker, runtime, monkeypatch, capsys):
    class Connector:
        capabilities = frozenset({Capability.POLL})

        async def poll(self, _cursor) -> PollResult:
            return PollResult(messages=[], cursor={}, ack=["1"])

        async def acknowledge(self, _ack) -> None:
            raise ConnectorError(f"could not acknowledge for {NAME}")

    monkeypatch.setattr(type(runtime), "connector_for", lambda _self, _source: Connector())
    async with runtime.sessions() as session:
        source = IngestSource(name=f"probe-{uuid.uuid4()}", connector="webhook")
        session.add(source)
        await session.commit()
        payload = {"source_id": str(source.id)}
        fresh_logs(monkeypatch, jobs)
        await worker._poll(runtime.system_ctx(session, "worker"), payload)

    assert_only_types(capsys.readouterr().out, "acknowledge_failed", "ConnectorError")


@pytest.mark.parametrize("emit", ["field", "nested", "event"])
def test_exception_objects_in_log_fields_become_types(capsys, emit):
    configure_logging("INFO")
    log = structlog.get_logger("zentral.test")
    exc = ValueError(f"failed for {NAME}")
    if emit == "field":
        log.error("probe", detail=exc)
    elif emit == "nested":
        log.error("probe", detail=[{"inner": exc}])
    else:
        log.error(exc)
    assert_only_types(capsys.readouterr().out, "ValueError")


def test_no_log_call_in_the_code_base_quotes_a_caught_exception():
    """Catches the next ``log.warning(..., reason=str(exc))``, in any form."""
    source_root = Path(zentral.__file__).parent
    found = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for handler in ast.walk(tree):
            if not (isinstance(handler, ast.ExceptHandler) and handler.name):
                continue
            for call in ast.walk(handler):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in {"log", "logger", "audit_log"}
                ):
                    continue
                allowed = {
                    id(arg)
                    for inner in ast.walk(call)
                    if isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id in {"type", "error_fields"}
                    for arg in inner.args
                }
                values = [*call.args, *(k.value for k in call.keywords if k.arg != "exc_info")]
                for value in values:
                    for name in ast.walk(value):
                        if (
                            isinstance(name, ast.Name)
                            and name.id == handler.name
                            and id(name) not in allowed
                        ):
                            found.append(f"{path.relative_to(source_root)}:{name.lineno}")
    assert not found, f"exception text in a log call: {found}"


# --- libraries --------------------------------------------------------------------------------


def starlette_lifespan_report(exc: Exception) -> None:
    # Starlette hands uvicorn a rendered traceback, which uvicorn logs as the message.
    text = "".join(traceback.format_exception(exc))
    logging.getLogger("uvicorn.error").error(text)


def uvicorn_logs_the_exception(exc: Exception) -> None:
    logging.getLogger("uvicorn.error").error(exc)


def library_uses_percent_args(exc: Exception) -> None:
    logging.getLogger("uvicorn.error").error("Error loading ASGI app factory: %s", exc)


def library_uses_an_f_string(exc: Exception) -> None:
    logging.getLogger("mcp.client").warning(f"Session termination failed: {exc}")


def asyncio_reports_a_task(exc: Exception) -> None:
    class Task:
        def __repr__(self) -> str:
            return f"<Task finished exception={exc!r}>"

    loop = asyncio.new_event_loop()
    try:
        loop.default_exception_handler(
            {"message": "Task exception was never retrieved", "exception": exc, "future": Task()}
        )
    finally:
        loop.close()


@pytest.mark.parametrize(
    "emit",
    [
        starlette_lifespan_report,
        uvicorn_logs_the_exception,
        library_uses_percent_args,
        library_uses_an_f_string,
        asyncio_reports_a_task,
    ],
)
def test_library_log_lines_carry_no_exception_text(capsys, emit):
    configure_logging("INFO")
    emit(chained_failure())
    out = capsys.readouterr().out
    assert out.strip(), "the failure must still leave a line"
    assert NAME not in out


def test_library_info_lines_stay_readable(capsys):
    configure_logging("INFO")
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    assert "Application startup complete." in capsys.readouterr().out


# --- what Python prints for an exception nobody caught -----------------------------------------

WORKER_CRASH = """
import zentral.observability as observability

def crash(_settings):
    who = "Margit" + " Joensen"
    try:
        raise ValueError(f"failed for {who}")
    except ValueError as exc:
        raise RuntimeError(f"failed for {who}") from exc

observability.init_sentry = crash
from zentral.cli import app
app(["worker"])
"""

THREAD_CRASH = """
import threading
from zentral.logging import install_crash_hooks

def crash():
    raise RuntimeError("failed for " + "Margit" + " Joensen")

install_crash_hooks()
thread = threading.Thread(target=crash)
thread.start()
thread.join()
"""

UNRAISABLE = """
from zentral.logging import install_crash_hooks

class Crash:
    def __del__(self):
        raise RuntimeError("failed for " + "Margit" + " Joensen")

install_crash_hooks()
value = Crash()
del value
"""


def run_python(code: str, env: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    settings = {k: v for k, v in os.environ.items() if not k.startswith("ZENTRAL_")}
    return subprocess.run(  # noqa: S603 - this test's own constant scripts
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=cwd,
        env={**settings, "ZENTRAL_ENV": env},
    )


def test_worker_crash_prints_type_and_frames_and_fails(tmp_path):
    done = run_python(WORKER_CRASH, "production", tmp_path)

    assert done.returncode != 0
    assert NAME not in done.stdout and NAME not in done.stderr
    for kept in ("RuntimeError", "ValueError", "direct cause", "in crash"):
        assert kept in done.stderr, f"{kept} must stay"


@pytest.mark.parametrize(
    ("code", "frame"),
    [(THREAD_CRASH, "in crash"), (UNRAISABLE, "in __del__")],
    ids=["thread", "unraisable"],
)
def test_other_uncaught_errors_print_no_message(tmp_path, code, frame):
    done = run_python(code, "production", tmp_path)

    assert NAME not in done.stdout and NAME not in done.stderr
    assert "RuntimeError" in done.stderr and frame in done.stderr


def test_worker_crash_keeps_the_message_in_development(tmp_path):
    done = run_python(WORKER_CRASH, "development", tmp_path)

    assert done.returncode != 0
    assert NAME in done.stderr


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
