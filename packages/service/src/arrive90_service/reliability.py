"""Typed service fallbacks that preserve explicit degraded-state semantics."""

from __future__ import annotations

from dataclasses import dataclass

from arrive90_service.contracts import (
    JourneyBackend,
    ModelUnavailableError,
    NormalizedJourneyRequest,
    SearchMaterials,
    SourceUnavailableError,
    Station,
)


@dataclass(frozen=True)
class ResilientJourneyBackend:
    """Use a named schedule fallback only for source and model failures."""

    primary: JourneyBackend
    schedule_fallback: JourneyBackend

    def stations(self) -> tuple[Station, ...]:
        return self.primary.stations()

    def search(self, request: NormalizedJourneyRequest) -> SearchMaterials:
        try:
            return self.primary.search(request)
        except (SourceUnavailableError, ModelUnavailableError):
            return self.schedule_fallback.search(request)
