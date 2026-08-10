"""In-process token-bucket rate limiting for Flask routes."""

from __future__ import annotations

import os
import threading
import time
from functools import wraps
from typing import Callable

from flask import jsonify, request, session


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.1, float(raw))
    except ValueError:
        return default


class TokenBucket:
    def __init__(self, rate_per_minute: float) -> None:
        self.rate_per_sec = rate_per_minute / 60.0
        self.capacity = max(1.0, rate_per_minute)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def allow(self) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.updated
            self.updated = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True, 0.0
            needed = 1.0 - self.tokens
            retry = needed / self.rate_per_sec if self.rate_per_sec > 0 else 60.0
            return False, max(1.0, retry)


class RateLimiter:
    """Per-(group, identity) token buckets."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = threading.Lock()
        self.limits = {
            "engine": _env_float("RATE_LIMIT_ENGINE", 30),
            "report": _env_float("RATE_LIMIT_REPORT", 10),
            "auth": _env_float("RATE_LIMIT_AUTH", 20),
        }

    def client_identity(self) -> str:
        uid = session.get("user_id")
        if uid is not None:
            return f"user:{uid}"
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return f"ip:{forwarded}"
        return f"ip:{request.remote_addr or 'unknown'}"

    def _bucket(self, group: str, identity: str) -> TokenBucket:
        key = (group, identity)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                rate = self.limits.get(group, 30)
                bucket = TokenBucket(rate)
                self._buckets[key] = bucket
                # Opportunistic prune to avoid unbounded growth
                if len(self._buckets) > 5000:
                    # Drop oldest half by recreating (simple; identity churn is rare)
                    keep = dict(list(self._buckets.items())[-2500:])
                    self._buckets.clear()
                    self._buckets.update(keep)
                    self._buckets[key] = bucket
            return bucket

    def check(self, group: str) -> tuple[bool, float]:
        identity = self.client_identity()
        return self._bucket(group, identity).allow()

    def limit(self, group: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            @wraps(fn)
            def wrapped(*args, **kwargs):
                ok, retry_after = self.check(group)
                if not ok:
                    resp = jsonify({
                        "error": "Rate limit exceeded",
                        "code": "rate_limited",
                        "retry_after": int(retry_after),
                    })
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(int(retry_after))
                    return resp
                return fn(*args, **kwargs)

            return wrapped

        return decorator


limiter = RateLimiter()
