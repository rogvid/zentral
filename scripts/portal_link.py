"""Print a one-time requester-portal sign-in link — development only.

    uv run python scripts/portal_link.py jane.doe@example.com

In production the link is e-mailed by the worker; this shortcut exists so the
portal can be tried locally (and tested end to end) without a mailbox. It
refuses to run unless the instance uses the development sign-in.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from zentral.config import get_settings
from zentral.db.models import Contact
from zentral.runtime import build_runtime
from zentral.security.crypto import get_cipher
from zentral.services import portal


async def main(email: str) -> str:
    settings = get_settings()
    if settings.auth_mode != "dev" or settings.is_production:
        raise SystemExit("refusing: this helper is for development instances only")
    runtime = build_runtime(settings)
    link: list[str] = []

    async def capture(message) -> None:  # type: ignore[no-untyped-def]
        link.append(message.body.split("\n\n")[1])

    try:
        async with runtime.sessions() as session:
            identity = get_cipher().blind_index("email", email)
            contact = await session.scalar(select(Contact).where(Contact.identity_hash == identity))
            if contact is None:
                raise SystemExit(f"no requester with the address {email}")
            ctx = runtime.system_ctx(session, "cli", channel="cli")
            await portal.send_link(ctx, contact.id, capture)
            await ctx.commit()
    finally:
        await runtime.aclose()
    return link[0]


if __name__ == "__main__":
    print(asyncio.run(main(sys.argv[1])))
