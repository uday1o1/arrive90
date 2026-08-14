"""Truthfully degraded schedule backend for the loopback API workflow."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    EligibilityManifest,
    HorizonSupportManifest,
    ScoringState,
)

from arrive90_service.contracts import (
    FeedStatus,
    NormalizedJourneyRequest,
    SearchMaterials,
    Station,
)


class LocalBlockedBackend:
    """Exercise schedule selection while the source-feasibility gate is failed."""

    def stations(self) -> tuple[Station, ...]:
        return (
            Station("demo-origin", "Demo origin"),
            Station("demo-destination", "Demo destination"),
        )

    def search(self, request: NormalizedJourneyRequest) -> SearchMaterials:
        leg = TransitLeg(
            "demo-pattern",
            "demo-route",
            0,
            "demo-trip",
            "demo-origin-platform",
            "demo-origin",
            "demo-destination-platform",
            "demo-destination",
            request.effective_ready_at_utc + timedelta(minutes=2),
            request.effective_ready_at_utc + timedelta(minutes=17),
            ("demo-origin-platform", "demo-destination-platform"),
        )
        itinerary = CandidateItinerary((leg,), ())
        manifest_hash = hashlib.sha256(itinerary.policy_key.encode()).hexdigest()
        score = CandidateScore(
            itinerary,
            0.0,
            "UNAVAILABLE_SOURCE_GATE",
            ("UNAVAILABLE_SOURCE_GATE",),
        )
        return SearchMaterials(
            (score,),
            DecisionContext(
                request.initial_query_cutoff_utc,
                "local-source-gate-blocked",
                "ALERT_MASK_V1",
                manifest_hash,
                ((itinerary.policy_key, True),),
            ),
            EligibilityManifest(
                frozenset({"UNAVAILABLE_SOURCE_GATE"}),
                frozenset(),
            ),
            HorizonSupportManifest(frozenset()),
            ScoringState.ABSTAINED,
            FeedStatus.ABSENT,
            "NO_ACCEPTED_MODEL",
            "historical_v1",
        )
