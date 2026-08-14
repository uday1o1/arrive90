import pytest
from arrive90_service.rate_limit import FixedWindowLimiter


def test_fixed_window_limit_and_bounded_key_eviction() -> None:
    limiter = FixedWindowLimiter(maximum_keys=2)
    assert limiter.allow("search", "a", now=0, limit=1, window_seconds=60)
    assert not limiter.allow("search", "a", now=1, limit=1, window_seconds=60)
    assert limiter.allow("search", "a", now=60, limit=1, window_seconds=60)
    assert limiter.allow("search", "b", now=60, limit=1, window_seconds=60)
    assert limiter.allow("search", "c", now=60, limit=1, window_seconds=60)
    assert limiter.allow("search", "a", now=61, limit=1, window_seconds=60)
    with pytest.raises(ValueError, match="positive"):
        FixedWindowLimiter(maximum_keys=0)
    with pytest.raises(ValueError, match="positive"):
        limiter.allow("x", "x", now=0, limit=0, window_seconds=60)
