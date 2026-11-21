"""Pattern-based PII redaction.

Used in three places:

1. before ticket text is sent to an AI model (data minimisation),
2. when a principal without ``ticket:read_pii`` reads ticket content,
3. as a logging safety net.

This is deliberately conservative, deterministic and dependency-free. It is a
*best-effort* control: free text can always contain personal data that no
pattern recognises (names, for instance). It reduces exposure; it does not
make text anonymous. See docs/compliance/gdpr.md.
"""

from __future__ import annotations

import re

# Every pattern below must run in linear time on hostile input; see
# tests/test_hardening.py::test_redaction_is_linear_on_hostile_input.

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Quantifiers are bounded on purpose: an unbounded local part makes this
    # quadratic on long runs of address characters without an "@" (a cheap way
    # to burn CPU, since redaction runs on attacker-supplied text).
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b")),
    ("URL", re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Danish CPR and similar date-based national identifiers (ddmmyy-xxxx).
    ("NATIONAL_ID", re.compile(r"\b\d{6}[- ]?\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("PHONE", re.compile(r"(?<![\w.])\+?\d[\d ()./-]{6,}\d(?![\w.])")),
    (
        "SECRET",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(['\"]?\s*(?:is|[:=])\s*)"
            # A quoted value may hold spaces: {'password': 'two words'}.
            r"(?:(\")[^\"\n]{0,512}\"|(')[^'\n]{0,512}'|\S+)"
        ),
    ),
)


def _mask_secret(match: re.Match[str]) -> str:
    quote = match.group(3) or match.group(4) or ""
    return f"{match.group(1)}{match.group(2)}{quote}[SECRET]{quote}"


def redact(text: str) -> str:
    """Replace recognised personal identifiers and secrets with typed placeholders."""
    if not text:
        return text
    for label, pattern in _PATTERNS:
        if label == "SECRET":
            text = pattern.sub(_mask_secret, text)
        else:
            text = pattern.sub(f"[{label}]", text)
    return text
