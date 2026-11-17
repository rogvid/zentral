"""Deterministic keyword classifier.

No model, no network, no data leaves the process. It is the default provider
and the automatic fallback whenever a model-backed provider fails, so ticket
intake never depends on AI availability.
"""

from __future__ import annotations

import re
from typing import ClassVar

from zentral.ai.base import AIProvider, Classification, ClassificationInput, Priority

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security": ("phishing", "malware", "virus", "ransomware", "breach", "hacked", "suspicious"),
    "access": (
        "password",
        "login",
        "log in",
        "sign in",
        "locked out",
        "mfa",
        "permission",
        "access",
    ),
    "network": ("vpn", "wifi", "wi-fi", "network", "internet", "dns", "firewall"),
    "hardware": (
        "laptop",
        "monitor",
        "keyboard",
        "mouse",
        "printer",
        "docking",
        "battery",
        "screen",
    ),
    "software": ("install", "update", "crash", "license", "excel", "outlook", "teams", "error"),
    "billing": ("invoice", "billing", "payment", "refund"),
    "incident": ("outage", "down", "unavailable", "not responding", "alert", "critical"),
    "service_request": ("request", "new user", "onboarding", "order", "please provide"),
}
_URGENT = (
    "outage",
    "everyone",
    "all users",
    "production down",
    "ransomware",
    "breach",
    "data loss",
)
_HIGH = ("urgent", "asap", "cannot work", "can't work", "blocked", "locked out", "critical")


class RulesProvider(AIProvider):
    name: ClassVar[str] = "rules"
    on_prem: ClassVar[bool] = True

    async def classify(self, inp: ClassificationInput) -> Classification:
        text = f"{inp.subject}\n{inp.body}".casefold()
        scores = {
            category: sum(len(re.findall(rf"\b{re.escape(word)}", text)) for word in words)
            for category, words in _KEYWORDS.items()
            if category in inp.categories
        }
        best = max(scores, key=lambda c: scores[c], default=None)
        priority: Priority = "normal"
        if any(word in text for word in _URGENT):
            priority = "urgent"
        elif any(word in text for word in _HIGH):
            priority = "high"
        system = _match_system(text, inp)
        if best is None or scores[best] == 0:
            fallback = "other" if "other" in inp.categories else inp.categories[0]
            return Classification(
                category=fallback, priority=priority, summary="", confidence=0.2, system=system
            )
        return Classification(
            category=best,
            priority=priority,
            summary="",
            confidence=min(0.9, 0.6 + 0.1 * scores[best]),
            system=system,
        )


def _match_system(text: str, inp: ClassificationInput) -> str:
    """A system matches when its key, name or one of its keywords occurs as a word.
    Ambiguous matches (two systems tie) yield no suggestion."""
    hits: dict[str, int] = {}
    for system in inp.systems:
        terms = {system.key, system.name, *system.keywords}
        count = sum(
            len(re.findall(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", text))
            for term in terms
            if term
        )
        if count:
            hits[system.key] = count
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return ""
    return ranked[0][0]
