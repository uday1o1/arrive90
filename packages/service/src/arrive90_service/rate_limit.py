"""Small bounded fixed-window limiter for the documented local scale."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock


@dataclass
class _Window:
    started: float
    count: int


class FixedWindowLimiter:
    def __init__(self, *, maximum_keys: int = 10_000) -> None:
        if maximum_keys <= 0:
            raise ValueError("maximum limiter keys must be positive")
        self._maximum_keys = maximum_keys
        self._windows: OrderedDict[tuple[str, str], _Window] = OrderedDict()
        self._lock = Lock()

    def allow(
        self,
        namespace: str,
        key: str,
        *,
        now: float,
        limit: int,
        window_seconds: int,
    ) -> bool:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("rate-limit bounds must be positive")
        identity = (namespace, key)
        with self._lock:
            window = self._windows.get(identity)
            if window is None or now - window.started >= window_seconds:
                self._windows[identity] = _Window(now, 1)
                self._windows.move_to_end(identity)
                while len(self._windows) > self._maximum_keys:
                    self._windows.popitem(last=False)
                return True
            self._windows.move_to_end(identity)
            if window.count >= limit:
                return False
            window.count += 1
            return True
