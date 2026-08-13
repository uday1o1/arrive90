"""Deterministic timetable and connectivity equivalence-class inventory."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from arrive90_routing.population import encode_key


@dataclass(frozen=True)
class EquivalenceClassInput:
    service_calendar_hash: str
    timetable_hash: str
    transfer_connectivity_hash: str
    window_start_local: str
    readiness_horizon_minutes: int

    @property
    def class_id(self) -> str:
        return hashlib.sha256(
            encode_key(
                (
                    self.service_calendar_hash,
                    self.timetable_hash,
                    self.transfer_connectivity_hash,
                    self.window_start_local,
                    str(self.readiness_horizon_minutes),
                )
            )
        ).hexdigest()


def enumerate_equivalence_classes(
    inputs: tuple[EquivalenceClassInput, ...],
) -> tuple[EquivalenceClassInput, ...]:
    by_identifier = {item.class_id: item for item in inputs}
    return tuple(by_identifier[key] for key in sorted(by_identifier, key=str.encode))
