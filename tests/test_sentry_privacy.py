"""What the application sends to Sentry must not identify anyone.

Runs the real application with the real Sentry options against an in-memory
transport, sends a request full of private data the way a reverse proxy would
forward it, and checks none of it is in anything the SDK tries to send.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
import pytest
import sentry_sdk
from fastapi import Request
from sentry_sdk.transport import Transport

from tests.conftest import make_settings
from zentral.api.app import create_app
from zentral.observability import capture_exception, sentry_options

PRIVATE = [
    "203.0.113.77",  # visitor address, from X-Forwarded-For / X-Real-IP
    "reprocookie123",
    "reprotoken456",
    "repro@example.com",
    "repro%40example.com",
    "hunter2",  # in a request body and a handler's local variables
    "chair",  # a search term in the query string
    "ZEN-4711",  # a ticket number in the path
    "Margit",  # personal data quoted by an exception message
]


class CaptureTransport(Transport):
    def __init__(self) -> None:
        super().__init__({"dsn": "http://key@localhost/1"})
        self.sent: list[str] = []

    def capture_envelope(self, envelope) -> None:  # type: ignore[no-untyped-def]
        for item in envelope.items:
            self.sent.append(item.payload.get_bytes().decode("utf-8", "replace"))


@pytest.fixture
def captured() -> Iterator[CaptureTransport]:
    transport = CaptureTransport()
    options = sentry_options(make_settings(sentry_release="test-release"))
    sentry_sdk.init(
        dsn="http://key@localhost/1", transport=transport, traces_sample_rate=None, **options
    )
    try:
        yield transport
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # back to a disabled client for the other tests


async def test_nothing_private_reaches_sentry(runtime, issuer, captured):
    app = create_app(runtime.settings, runtime=runtime, validator=issuer.validator())

    async def leaky(ticket: str, request: Request) -> None:
        login = await request.json()  # a local variable holding the password
        raise RuntimeError(f"failed for {login['name']} on {ticket}: {login}")

    async def fine(ticket: str) -> dict[str, str]:
        return {"ticket": ticket}

    app.add_api_route("/api/v1/_probe/{ticket}", leaky, methods=["POST"])
    app.add_api_route("/api/v1/_probe/{ticket}", fine, methods=["GET"])
    # Ahead of the web client's catch-all route.
    app.router.routes[:0] = [app.router.routes.pop(), app.router.routes.pop()]

    headers = {
        "X-Forwarded-For": "203.0.113.77",
        "X-Real-IP": "203.0.113.77",
        "Cookie": "session=reprocookie123",
        "Authorization": "Bearer reprotoken456",
    }
    query = "?email=repro%40example.com&q=chair"
    logging.getLogger("zentral.probe").error("failed for repro@example.com")
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False, client=("203.0.113.77", 4711)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ok = await client.get(f"/api/v1/_probe/ZEN-4711{query}", headers=headers)
        assert ok.status_code == 200
        body = {"name": "Margit", "email": "repro@example.com", "password": "hunter2"}
        failed = await client.post(f"/api/v1/_probe/ZEN-4711{query}", json=body, headers=headers)
        assert failed.status_code == 500
    sentry_sdk.flush()

    sent = "\n".join(captured.sent)
    assert "RuntimeError" in sent, "the error itself must still be reported"
    assert "leaky" in sent, "its stack frames must still be reported"
    assert sent.count('"user":{"geo":{}}') >= 2, "an empty location stops Sentry adding a city"
    leaks = {value: sent.count(value) for value in PRIVATE if value in sent}
    assert not leaks, f"private data sent to Sentry: {leaks}\n{json.dumps(captured.sent)[:3000]}"


def test_handled_failure_keeps_type_and_drops_message(captured):
    address = "@".join(("repro", "example.com"))  # not in the source context sent with frames
    try:
        raise ValueError(f"could not notify {address}")
    except ValueError as exc:
        capture_exception(exc)
    sentry_sdk.flush()

    sent = "\n".join(captured.sent)
    assert "ValueError" in sent
    assert "repro@example.com" not in sent
