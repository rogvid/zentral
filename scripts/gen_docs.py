"""Generate the reference tables that must never drift from the code.

    uv run python scripts/gen_docs.py          # rewrite the generated blocks
    uv run python scripts/gen_docs.py --check  # exit 1 if docs are out of date (CI)

Blocks are delimited by ``<!-- BEGIN GENERATED: name -->`` / ``<!-- END GENERATED -->``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pydantic import SecretStr

from zentral.config import Settings
from zentral.security.rbac import ROLE_PERMISSIONS, Permission, Role

ROOT = Path(__file__).parents[1]

PERMISSION_HELP = {
    Permission.TICKET_READ: "Read tickets (within team scope)",
    Permission.TICKET_READ_PII: "See requester identity and unmasked content",
    Permission.TICKET_READ_ALL: "See tickets of every team",
    Permission.TICKET_TRIAGE: "See tickets not yet assigned to a team",
    Permission.TICKET_CREATE: "Create tickets",
    Permission.TICKET_UPDATE: "Change status, priority, category, system, tags; route to a team",
    Permission.TICKET_ASSIGN: "Assign a ticket to a person",
    Permission.TICKET_REPLY: "Send a message to the requester",
    Permission.TICKET_NOTE: "Add internal notes",
    Permission.TEAM_MANAGE: "Manage teams and membership",
    Permission.SYSTEM_MANAGE: "Manage the systems catalogue",
    Permission.SOURCE_MANAGE: "Manage channels and their secrets",
    Permission.RULE_MANAGE: "Manage automation rules",
    Permission.AUDIT_READ: "Read and verify the audit trail",
    Permission.PRIVACY_EXPORT: "Look up a person and export their data",
    Permission.PRIVACY_ERASE: "Erase a person's data",
    Permission.INGEST_WRITE: "Submit issues as a service",
}


def rbac_table() -> str:
    roles = list(Role)
    head = "| Permission | What it allows | " + " | ".join(r.value.removeprefix("Zentral.") for r in roles) + " |"
    rule = "|---|---|" + "|".join(":-:" for _ in roles) + "|"
    rows = [
        f"| `{p.value}` | {PERMISSION_HELP[p]} | "
        + " | ".join("✓" if p in ROLE_PERMISSIONS[r] else "" for r in roles)
        + " |"
        for p in Permission
    ]
    return "\n".join([head, rule, *rows])


def config_table() -> str:
    rows = ["| Variable | Default | Type |", "|---|---|---|"]
    for name, field in Settings.model_fields.items():
        default = field.get_default(call_default_factory=True)
        secret = "Secret" in str(field.annotation)
        if secret or isinstance(default, SecretStr):
            shown = "*(secret, none)*"
        elif default in ("", [], {}):
            shown = "*(empty)*"
        elif isinstance(default, list):
            shown = "`" + ",".join(map(str, default)) + "`"
        else:
            shown = f"`{default}`"
        kind = re.sub(r"typing\.|<class '|'>|Annotated\[|, NoDecode.*", "", str(field.annotation))
        kind = kind.replace("pydantic.types.", "").replace("Literal", "one of ")
        rows.append(f"| `ZENTRAL_{name.upper()}` | {shown} | {kind} |")
    return "\n".join(rows)


BLOCKS = {
    "docs/compliance/rbac.md": {"rbac": rbac_table},
    "docs/operations/configuration.md": {"config": config_table},
}


def render(text: str, generators: dict) -> str:
    for name, generate in generators.items():
        pattern = re.compile(
            rf"(<!-- BEGIN GENERATED: {name} -->\n).*?(\n<!-- END GENERATED -->)", re.DOTALL
        )
        if not pattern.search(text):
            raise SystemExit(f"marker for {name!r} not found")
        text = pattern.sub(lambda m, g=generate: m.group(1) + g() + m.group(2), text)
    return text


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for relative, generators in BLOCKS.items():
        path = ROOT / relative
        current = path.read_text(encoding="utf-8")
        updated = render(current, generators)
        if updated != current:
            stale.append(relative)
            if not check:
                path.write_text(updated, encoding="utf-8")
    if stale and check:
        print("out of date (run scripts/gen_docs.py):", ", ".join(stale))
        return 1
    print("docs up to date" if not stale else "updated: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
