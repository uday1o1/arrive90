import threading

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


def test_concurrent_callers_cannot_exceed_one_shared_budget() -> None:
    limiter = FixedWindowLimiter()
    barrier = threading.Barrier(101)
    results: list[bool] = []

    def attempt() -> None:
        barrier.wait()
        results.append(limiter.allow("search", "same-client", now=1, limit=30, window_seconds=60))

    threads = [threading.Thread(target=attempt) for _index in range(100)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sum(results) == 30
