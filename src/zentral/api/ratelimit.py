"""Fixed-window in-memory rate limiter.

A last line of defence inside each process. It is not shared between replicas;
put the service behind a gateway or WAF for global limits (see
docs/architecture/security.md).
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, max_keys: int = 50_000) -> None:
        self._windows: dict[str, tuple[int, int]] = {}
        self._max_keys = max_keys

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        window = int(time.monotonic()) // window_seconds
        current, count = self._windows.get(key, (window, 0))
        if current != window:
            count = 0
        if count >= limit:
            return False
        if len(self._windows) >= self._max_keys:
            # Bounded memory: drop expired windows (all of them if none expired).
            self._windows = {k: v for k, v in self._windows.items() if v[0] == window}
            if len(self._windows) >= self._max_keys:
                self._windows.clear()
        self._windows[key] = (window, count + 1)
        return True
