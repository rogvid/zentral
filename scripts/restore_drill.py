"""Backup/restore drill: back up, restore into a scratch database, prove it, time it.

    uv run python scripts/restore_drill.py postgresql://zentral@db.internal/zentral \\
        [--escrow-keyring /media/escrow/keyring-escrow.json \\
         --escrow-kek /media/escrow/zentral-escrow.kek]

Run it in an administrator shell that has the installation's ``ZENTRAL_*``
settings (key provider and keyring, or plain keys) — the same shell you would
restore from. It never writes to the source database:

1. ``pg_dump`` the source (custom format) — the backup;
2. create ``<name>_drill_<timestamp>`` on the same server and ``pg_restore`` into it;
3. ``zentral doctor`` against the copy: schema, and that the keys decrypt stored data;
4. ``zentral audit verify`` against the copy: the hash chain is intact;
5. ``zentral keys verify-escrow`` (optional): the offline escrow copy opens to the
   same keys;
6. drop the copy and delete the dump — both contain personal data.

Prints a timing table to paste into the drill log (docs/runbooks/backup-restore.md).
Needs ``pg_dump``, ``pg_restore``, ``createdb`` and ``dropdb`` matching the server.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def asyncpg(url: str) -> str:
    parts = urlsplit(url)
    query = parts.query.replace("sslmode=", "ssl=")
    return urlunsplit(parts._replace(scheme="postgresql+asyncpg", query=query))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="libpq URL of the database to back up")
    parser.add_argument("--escrow-keyring", type=Path)
    parser.add_argument("--escrow-kek", type=Path)
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    args = parser.parse_args()
    if bool(args.escrow_keyring) != bool(args.escrow_kek):
        parser.error("--escrow-keyring and --escrow-kek go together")
    for tool in ("pg_dump", "pg_restore", "createdb", "dropdb"):
        if shutil.which(tool) is None:
            parser.error(f"{tool} is not installed")

    source_db = urlsplit(args.source).path.lstrip("/")
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    scratch = f"{source_db}_drill_{stamp}"
    scratch_url = with_database(args.source, scratch)
    maintenance = with_database(args.source, "postgres")
    env = {**os.environ, "ZENTRAL_DATABASE_URL": asyncpg(scratch_url)}
    rows: list[tuple[str, float, str]] = []
    failed = False

    def step(name: str, command: list[str], **kwargs: object) -> bool:
        nonlocal failed
        start = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)  # type: ignore[call-overload]  # noqa: S603
        seconds = time.perf_counter() - start
        last = (result.stdout.strip() or result.stderr.strip()).splitlines()
        outcome = "ok" if result.returncode == 0 else "FAILED"
        detail = f"{outcome}: {last[-1]}" if last else outcome
        rows.append((name, seconds, detail[:120]))
        if result.returncode != 0:
            failed = True
            sys.stderr.write(f"--- {name} failed\n{result.stdout}{result.stderr}\n")
        return result.returncode == 0

    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "zentral.dump"
        try:
            if step(
                "backup (pg_dump)", ["pg_dump", "--format=custom", f"--file={dump}", args.source]
            ):
                size = dump.stat().st_size / 1_048_576
                rows[-1] = (rows[-1][0], rows[-1][1], f"ok: {size:.1f} MiB")
                step(
                    "create scratch database",
                    ["createdb", f"--maintenance-db={maintenance}", scratch],
                )
                step(
                    "restore (pg_restore)",
                    ["pg_restore", "--no-owner", f"--dbname={scratch_url}", str(dump)],
                )
                step("zentral doctor (schema, decryption)", ["zentral", "doctor"], env=env)
                step("zentral audit verify", ["zentral", "audit", "verify"], env=env)
                if args.escrow_keyring:
                    step(
                        "escrow keyring matches",
                        [
                            "zentral", "keys", "verify-escrow",
                            "--keyring", str(args.escrow_keyring),
                            "--kek-file", str(args.escrow_kek),
                        ],
                        env=env,
                    )  # fmt: skip
        finally:
            if not args.keep:
                step(
                    "drop scratch database",
                    ["dropdb", f"--maintenance-db={maintenance}", "--if-exists", scratch],
                )

    total = sum(seconds for _, seconds, _ in rows)
    print(f"Restore drill {datetime.now(UTC):%Y-%m-%d %H:%M} UTC — {source_db} → {scratch}\n")
    print("| Step | Seconds | Result |\n|---|---:|---|")
    for name, seconds, outcome in rows:
        print(f"| {name} | {seconds:.1f} | {outcome} |")
    print(f"| **total** | **{total:.1f}** | {'FAILED' if failed else 'passed'} |")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
