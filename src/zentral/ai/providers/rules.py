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
    "access": ("password", "login", "log in", "sign in", "locked out", "mfa", "permission", "access"),
    "network": ("vpn", "wifi", "wi-fi", "network", "internet", "dns", "firewall"),
    "hardware": ("laptop", "monitor", "keyboard", "mouse", "printer", "docking", "battery", "screen"),
    "software": ("install", "update", "crash", "license", "excel", "outlook", "teams", "error"),
    "billing": ("invoice", "billing", "payment", "refund"),
    "incident": ("outage", "down", "unavailable", "not responding", "alert", "critical"),
    "service_request": ("request", "new user", "onboarding", "order", "please provide"),
}
_URGENT = ("outage", "everyone", "all users", "production down", "ransomware", "breach", "data loss")
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
        if best is None or scores[best] == 0:
            fallback = "other" if "other" in inp.categories else inp.categories[0]
            return Classification(category=fallback, priority=priority, summary="", confidence=0.2)
        return Classification(
            category=best,
            priority=priority,
            summary="",
            confidence=min(0.9, 0.6 + 0.1 * scores[best]),
        )
