"""Races found by the load test (scripts/loadtest.py)."""

from __future__ import annotations

from zentral.services.tickets import get_or_create_contact


async def test_concurrent_first_contact_from_the_same_person(runtime, monkeypatch):
    """Two deliveries from a new requester at once: one contact, no error."""
    async with runtime.sessions() as first:
        ctx = runtime.system_ctx(first, "test")
        winner = await get_or_create_contact(
            ctx, "email", "new.person@example.com", email="new.person@example.com", name="New"
        )
        await ctx.commit()
        winner_id = winner.id

    async with runtime.sessions() as second:
        ctx = runtime.system_ctx(second, "test")
        # The second request looked before the first committed: it saw nobody.
        original = second.scalar
        calls = 0

        async def stale_first_lookup(*args, **kwargs):
            nonlocal calls
            calls += 1
            return None if calls == 1 else await original(*args, **kwargs)

        monkeypatch.setattr(second, "scalar", stale_first_lookup)
        contact = await get_or_create_contact(
            ctx, "email", "new.person@example.com", email="new.person@example.com", name="New"
        )
        await ctx.commit()
        assert contact.id == winner_id


def test_several_server_processes_need_real_sign_in(monkeypatch):
    """Dev tokens are signed per process, so dev mode cannot run several workers."""
    import uvicorn
    from cryptography.fernet import Fernet
    from typer.testing import CliRunner

    from zentral.cli import app
    from zentral.config import get_settings

    for name in list(__import__("os").environ):
        if name.startswith("ZENTRAL_"):
            monkeypatch.delenv(name)
    for name, value in {
        "ZENTRAL_AUTH_MODE": "dev",
        "ZENTRAL_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "ZENTRAL_BLIND_INDEX_KEY": "b" * 40,
        "ZENTRAL_AUDIT_HMAC_KEY": "a" * 40,
    }.items():
        monkeypatch.setenv(name, value)
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k))
    get_settings.cache_clear()
    try:
        refused = CliRunner().invoke(app, ["serve", "--workers", "2"])
        assert refused.exit_code == 2 and "needs real sign-in" in refused.output
        assert started == []
        assert CliRunner().invoke(app, ["serve"]).exit_code == 0
        assert started[0]["workers"] == 1
    finally:
        get_settings.cache_clear()
