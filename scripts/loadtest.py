"""Load test a development instance: agents, ingestion bursts, the portal, attachments.

    uv run python scripts/loadtest.py http://127.0.0.1:8000 --users 40 --duration 120

Needs an instance with ``ZENTRAL_AUTH_MODE=dev`` (persona tokens), the portal
enabled, and — for the portal scenario — the same ``ZENTRAL_*`` environment in
this shell (sign-in links are minted with ``scripts/portal_link.py``). Never
point it at production: it creates thousands of tickets.

Each virtual user loops over a weighted mix of operations with a short think
time; latencies are reported per operation (p50 / p95 / p99), plus throughput
and errors. ``--seed`` tickets are created first so lists and search run
against a realistic volume. See docs/operations/sizing.md for results.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).parents[1]
PORTAL = {"X-Zentral-Portal": "1"}  # the portal's CSRF header
WORDS = [
    "printer",
    "vpn",
    "password",
    "invoice",
    "refund",
    "laptop",
    "screen",
    "monitor",
    "outlook",
    "teams",
    "mailbox",
    "access",
    "badge",
    "payslip",
    "onboarding",
    "database",
    "disk",
    "alert",
    "backup",
    "slow",
    "error",
    "crash",
]


@dataclass
class Stats:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    statuses: dict[str, dict[int, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )

    def record(self, op: str, seconds: float, status: int) -> None:
        self.latencies[op].append(seconds)
        if status >= 400:
            self.errors[op] += 1
            self.statuses[op][status] += 1


def png(size: int) -> bytes:
    """A valid PNG of roughly ``size`` bytes (uploads are content-checked)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    width = 512
    rows = max(1, size // (width * 3))
    raw = b"".join(b"\x00" + random.randbytes(width * 3) for _ in range(rows))
    header = struct.pack(">IIBBBBB", width, rows, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def text(n: int) -> str:
    return " ".join(random.choice(WORDS) for _ in range(n))


@dataclass
class Shared:
    bases: list[str]
    agents: dict[str, dict[str, str]]  # per server: dev tokens are per process
    agent: dict[str, str]
    admin: dict[str, str]
    hook_url: str
    hook: dict[str, str]
    ticket_ids: list[int]
    portal_cookies: list[httpx.Cookies]
    requesters: list[str]
    stats: Stats
    attachment: bytes


async def timed(stats: Stats, op: str, call: Awaitable[httpx.Response]) -> httpx.Response | None:
    start = time.perf_counter()
    try:
        response = await call
    except httpx.HTTPError:
        stats.record(op, time.perf_counter() - start, 599)
        return None
    stats.record(op, time.perf_counter() - start, response.status_code)
    return response


# --- operations ---------------------------------------------------------------------


async def ingest(http: httpx.AsyncClient, s: Shared, email: str | None = None) -> None:
    email = email or random.choice(s.requesters)
    body = {
        "title": f"{text(4)} ({random.randint(1, 10**9)})",
        "description": text(60) + "\n\nCall me on +45 12 34 56 78.",
        "requester_email": email,
        "requester_name": email.split("@")[0],
        "severity": random.choice(["low", "normal", "high"]),
    }
    if random.random() < 0.2:  # repeated alerts thread onto one ticket
        body["thread_key"] = f"alert:{random.randint(1, 50)}"
    await timed(s.stats, "ingest (webhook)", http.post(s.hook_url, headers=s.hook, json=body))


async def queue(http: httpx.AsyncClient, s: Shared) -> None:
    params = random.choice(
        [
            {"status": ["new", "open"]},
            {"unassigned": "true"},
            {"priority": "high"},
            {"overdue": "true"},
            {},
        ]
    )
    await timed(s.stats, "list queue", http.get("/api/v1/tickets", headers=s.agent, params=params))


async def search(http: httpx.AsyncClient, s: Shared) -> None:
    await timed(
        s.stats,
        "search (q=)",
        http.get("/api/v1/tickets", headers=s.agent, params={"q": random.choice(WORDS)}),
    )


async def open_ticket(http: httpx.AsyncClient, s: Shared) -> None:
    tid = random.choice(s.ticket_ids)
    await timed(s.stats, "open ticket", http.get(f"/api/v1/tickets/{tid}", headers=s.agent))
    await timed(
        s.stats, "ticket messages", http.get(f"/api/v1/tickets/{tid}/messages", headers=s.agent)
    )


async def note(http: httpx.AsyncClient, s: Shared) -> None:
    tid = random.choice(s.ticket_ids)
    await timed(
        s.stats,
        "add note",
        http.post(
            f"/api/v1/tickets/{tid}/messages",
            headers=s.agent,
            json={"kind": "note", "body": text(30)},
        ),
    )


async def update(http: httpx.AsyncClient, s: Shared) -> None:
    tid = random.choice(s.ticket_ids)
    await timed(
        s.stats,
        "update ticket",
        http.patch(
            f"/api/v1/tickets/{tid}",
            headers=s.agent,
            json={"priority": random.choice(["low", "normal", "high"])},
        ),
    )


async def insights(http: httpx.AsyncClient, s: Shared) -> None:
    await timed(s.stats, "insights", http.get("/api/v1/tickets/insights", headers=s.agent))


async def attach(http: httpx.AsyncClient, s: Shared) -> None:
    tid = random.choice(s.ticket_ids)
    await timed(
        s.stats,
        "upload attachment",
        http.post(
            f"/api/v1/tickets/{tid}/attachments",
            headers={**s.agent, "Content-Type": "application/octet-stream"},
            params={"filename": "screenshot.png"},
            content=s.attachment,
        ),
    )


async def portal(http: httpx.AsyncClient, s: Shared) -> None:
    if not s.portal_cookies:
        return
    cookies = random.choice(s.portal_cookies)
    r = await timed(
        s.stats,
        "portal: my tickets",
        http.get("/api/v1/portal/tickets", cookies=cookies, headers=PORTAL),
    )
    if r is not None and r.status_code == 200 and random.random() < 0.3:
        await timed(
            s.stats,
            "portal: new ticket",
            http.post(
                "/api/v1/portal/tickets",
                cookies=cookies,
                headers=PORTAL,
                json={"subject": text(5), "body": text(40)},
            ),
        )


MIX: list[tuple[Callable[[httpx.AsyncClient, Shared], Awaitable[None]], int]] = [
    (ingest, 25),
    (queue, 25),
    (open_ticket, 20),
    (search, 5),
    (note, 8),
    (update, 7),
    (insights, 3),
    (attach, 2),
    (portal, 5),
]


async def user(s: Shared, deadline: float, think: float, index: int) -> None:
    ops, weights = zip(*MIX, strict=True)
    base = s.bases[index % len(s.bases)]
    s = replace(s, agent=s.agents[base])
    async with httpx.AsyncClient(base_url=base, timeout=60) as http:
        while time.monotonic() < deadline:
            await random.choices(ops, weights)[0](http, s)
            await asyncio.sleep(random.uniform(0, 2 * think))


# --- setup ----------------------------------------------------------------------


def portal_sessions(base: str, emails: list[str]) -> list[httpx.Cookies]:
    jars = []
    with httpx.Client(base_url=base, timeout=30) as http:
        for email in emails:
            out = subprocess.run(  # noqa: S603
                [sys.executable, str(ROOT / "scripts/portal_link.py"), email],
                capture_output=True,
                text=True,
                check=False,
            )
            link = out.stdout.strip().splitlines()[-1] if out.returncode == 0 else ""
            parts = urlsplit(link)
            token = (parse_qs(parts.fragment) or parse_qs(parts.query)).get("token", [""])[0]
            if not token:
                print(f"  portal: no sign-in link for {email} ({out.stderr.strip()[-80:]})")
                continue
            r = http.post("/api/v1/portal/session", json={"token": token}, headers=PORTAL)
            if r.status_code == 200:
                jars.append(r.cookies)
    return jars


async def setup(bases: list[str], seed: int, portal_users: int) -> Shared:
    base = bases[0]
    agents: dict[str, dict[str, str]] = {}
    async with httpx.AsyncClient(timeout=60) as anywhere:
        for b in bases:
            r = await anywhere.post(f"{b}/api/v1/dev/token", json={"persona": "agent"})
            r.raise_for_status()
            agents[b] = {"Authorization": f"Bearer {r.json()['access_token']}"}
            await anywhere.get(f"{b}/api/v1/me", headers=agents[b])
    async with httpx.AsyncClient(base_url=base, timeout=60) as http:

        async def token(persona: str) -> dict[str, str]:
            r = await http.post("/api/v1/dev/token", json={"persona": persona})
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        admin, agent = await token("admin"), agents[base]
        r = await http.post(
            "/api/v1/sources",
            headers=admin,
            json={"name": f"Load test {int(time.time())}", "connector": "webhook"},
        )
        r.raise_for_status()
        source = r.json()
        shared = Shared(
            bases=bases,
            agents=agents,
            agent=agent,
            admin=admin,
            hook_url=f"/api/v1/hooks/{source['id']}",
            hook={"Authorization": f"Bearer {source['ingest_token']}"},
            ticket_ids=[],
            portal_cookies=[],
            requesters=[f"user{i}@example.com" for i in range(500)],
            stats=Stats(),
            attachment=png(200_000),
        )
        print(f"seeding {seed} tickets …", flush=True)
        sem = asyncio.Semaphore(20)

        async def one(i: int) -> None:
            async with sem:
                await ingest(http, shared, shared.requesters[i % len(shared.requesters)])

        await asyncio.gather(*(one(i) for i in range(seed)))
        for offset in range(0, 1000, 100):
            listing = await http.get(
                "/api/v1/tickets", headers=agent, params={"limit": 100, "offset": offset}
            )
            items = listing.json()["items"]
            shared.ticket_ids += [t["id"] for t in items]
            if len(items) < 100:
                break
    if portal_users:
        shared.portal_cookies = portal_sessions(base, shared.requesters[:portal_users])
        print(f"portal sessions: {len(shared.portal_cookies)}")
    shared.stats = Stats()  # the seeding is not part of the measurement
    return shared


def report(stats: Stats, elapsed: float, users: int) -> str:
    lines = [
        f"{users} virtual users, {elapsed:.0f} s",
        "",
        "| Operation | Requests | req/s | p50 ms | p95 ms | p99 ms | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    total = 0
    for op in sorted(stats.latencies, key=lambda o: -len(stats.latencies[o])):
        values = sorted(stats.latencies[op])
        total += len(values)
        q = statistics.quantiles(values, n=100) if len(values) > 1 else values * 99
        errors = stats.errors.get(op, 0)
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(stats.statuses[op].items()))
        lines.append(
            f"| {op} | {len(values)} | {len(values) / elapsed:.1f} | {q[49] * 1000:.0f} | "
            f"{q[94] * 1000:.0f} | {q[98] * 1000:.0f} | {errors}{f' ({detail})' if detail else ''} |"
        )
    lines.append(f"| **all** | {total} | **{total / elapsed:.1f}** | | | | |")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "base",
        nargs="?",
        default="http://127.0.0.1:8000",
        help="server URL; several, comma-separated, to spread users over processes",
    )
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--think", type=float, default=0.5, help="mean pause between operations")
    parser.add_argument("--seed", type=int, default=2000, help="tickets to create first")
    parser.add_argument("--portal-users", type=int, default=20)
    args = parser.parse_args()

    bases = [b.strip() for b in args.base.split(",") if b.strip()]
    shared = await setup(bases, args.seed, args.portal_users)
    print(f"running {args.users} users for {args.duration:.0f} s …", flush=True)
    start = time.monotonic()
    deadline = start + args.duration
    await asyncio.gather(*(user(shared, deadline, args.think, i) for i in range(args.users)))
    print(report(shared.stats, time.monotonic() - start, args.users))
    if len(bases) > 1:
        print(f"\nspread over {len(bases)} server processes")


if __name__ == "__main__":
    asyncio.run(main())
