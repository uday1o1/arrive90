"""A fail-closed point-in-time access boundary over primitive records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from arrive90_data_contracts.realtime import require_utc


class FutureAccessError(LookupError):
    """Raised when code explicitly requests a record unavailable at the cutoff."""


@dataclass(frozen=True)
class TemporalRecord[T]:
    key: str
    event_time_utc: datetime
    product_available_at_utc: datetime
    value: T

    def __post_init__(self) -> None:
        require_utc(self.event_time_utc, "event_time_utc")
        require_utc(self.product_available_at_utc, "product_available_at_utc")


class TemporalView[T]:
    """Expose only primitives whose product availability is at or before one cutoff."""

    def __init__(self, records: Iterable[TemporalRecord[T]], cutoff_utc: datetime) -> None:
        require_utc(cutoff_utc, "cutoff_utc")
        self.cutoff_utc = cutoff_utc
        self._records = tuple(records)

    def available(self) -> tuple[TemporalRecord[T], ...]:
        return tuple(
            record for record in self._records if record.product_available_at_utc <= self.cutoff_utc
        )

    def get(self, key: str) -> TemporalRecord[T]:
        matches = [record for record in self._records if record.key == key]
        if not matches:
            raise KeyError(key)
        eligible = [
            record for record in matches if record.product_available_at_utc <= self.cutoff_utc
        ]
        if not eligible:
            raise FutureAccessError(f"{key} is unavailable at the temporal cutoff")
        return max(eligible, key=lambda record: record.product_available_at_utc)
