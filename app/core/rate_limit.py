from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from .settings import get_settings


class AuthRateLimiter:
    """Small in-process brute-force guard for a single Fire2API instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._blocked_until: dict[tuple[str, str], float] = {}

    def retry_after(self, scope: str, remote: str) -> int:
        key = (scope, remote)
        now = time.monotonic()
        with self._lock:
            blocked_until = self._blocked_until.get(key, 0.0)
            if blocked_until <= now:
                self._blocked_until.pop(key, None)
                return 0
            return max(1, math.ceil(blocked_until - now))

    def register_failure(self, scope: str, remote: str) -> int:
        settings = get_settings()
        key = (scope, remote)
        now = time.monotonic()
        cutoff = now - settings.auth_rate_limit_window_seconds
        with self._lock:
            failures = self._failures[key]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            failures.append(now)
            if len(failures) < settings.auth_rate_limit_attempts:
                return 0
            failures.clear()
            self._blocked_until[key] = now + settings.auth_rate_limit_lockout_seconds
            return settings.auth_rate_limit_lockout_seconds

    def register_success(self, scope: str, remote: str) -> None:
        key = (scope, remote)
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
            self._blocked_until.clear()


auth_rate_limiter = AuthRateLimiter()
