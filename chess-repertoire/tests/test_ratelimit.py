"""Rate limiter unit tests."""

from __future__ import annotations

from webapp.ratelimit import TokenBucket


def test_token_bucket_allows_then_blocks():
    bucket = TokenBucket(rate_per_minute=60)  # 1/sec, capacity 60
    bucket.tokens = 2.0
    ok, _ = bucket.allow()
    assert ok
    ok, _ = bucket.allow()
    assert ok
    ok, retry = bucket.allow()
    assert not ok
    assert retry >= 1.0
