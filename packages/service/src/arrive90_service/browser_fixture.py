"""Deterministic browser-test backend with explicitly synthetic evidence."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from arrive90_data_contracts.candidates import CandidateItinerary, TransitLeg
from arrive90_decision.contracts import (
    CandidateScore,
    DecisionContext,
    EligibilityManifest,
    HorizonSupportManifest,
    QuantileEstimate,
    RecoveryTriggerInput,
    ScoringState,
    TripState,
)

from arrive90_service.contracts import (
    FeedStatus,
    NormalizedJourneyRequest,
    RecoveryMaterials,
    RecoveryRequest,
    SearchMaterials,
    Station,
)


def _direct(request: NormalizedJourneyRequest) -> CandidateItinerary:
    origin = request.origin_station_id
    leg = TransitLeg(
        "fixture-direct-pattern",
        "Red",
        0,
        "fixture-direct-trip",
        f"{origin}-platform",
        origin,
        "bravo-platform",
        "bravo",
        request.effective_ready_at_utc + timedelta(minutes=1),
        request.effective_ready_at_utc + timedelta(minutes=12),
        (f"{origin}-platform", "bravo-platform"),
    )
    return CandidateItinerary((leg,), ())


def _transfer(
    request: NormalizedJourneyRequest, *, suffix: str, arrival_minutes: int
) -> CandidateItinerary:
    origin = request.origin_station_id
    first = TransitLeg(
        f"fixture-first-{suffix}",
        "Red",
        0,
        f"fixture-first-trip-{suffix}",
        f"{origin}-platform",
        origin,
        f"park-red-{suffix}",
        "park",
        request.effective_ready_at_utc + timedelta(minutes=1),
        request.effective_ready_at_utc + timedelta(minutes=8),
        (f"{origin}-platform", f"park-red-{suffix}"),
    )
    second = TransitLeg(
        f"fixture-second-{suffix}",
        "Orange",
        1,
        f"fixture-second-trip-{suffix}",
        f"park-orange-{suffix}",
        "park",
        "bravo-platform",
        "bravo",
        request.effective_ready_at_utc + timedelta(minutes=10),
        request.effective_ready_at_utc + timedelta(minutes=arrival_minutes),
        (f"park-orange-{suffix}", "bravo-platform"),
    )
    return CandidateItinerary((first, second), (60,))


class BrowserFixtureBackend:
    """Exercise complete UI paths without representing the fixture as MBTA evidence."""

    def stations(self) -> tuple[Station, ...]:
        return (
            Station("alpha", "Alpha"),
            Station("alpha-stale", "Alpha - stale fixture"),
            Station("alpha-absent", "Alpha - absent fixture"),
            Station("alpha-sparse", "Alpha - sparse-support fixture"),
            Station("bravo", "Bravo"),
        )

    def search(self, request: NormalizedJourneyRequest) -> SearchMaterials:
        direct = _direct(request)
        transfer = _transfer(request, suffix="primary", arrival_minutes=16)
        backup = _transfer(request, suffix="backup", arrival_minutes=18)
        scores = (
            CandidateScore(
                direct,
                0.75,
                "deadline-band-0.70-0.80",
                ("line-red", "station-origin", "station-bravo"),
            ),
            CandidateScore(
                transfer,
                0.94,
                "deadline-band-0.90-0.95",
                ("line-red", "line-orange", "station-origin", "station-bravo", "station-park"),
                (
                    QuantileEstimate(
                        "p50",
                        request.effective_ready_at_utc + timedelta(minutes=14),
                        "quantile-p50",
                    ),
                    QuantileEstimate(
                        "p90",
                        request.effective_ready_at_utc + timedelta(minutes=18),
                        "quantile-p90",
                    ),
                ),
            ),
            CandidateScore(
                backup,
                0.93,
                "deadline-band-0.90-0.95",
                ("line-red", "line-orange", "station-origin", "station-bravo", "station-park"),
            ),
        )
        all_cells = frozenset(
            cell
            for score in scores
            for cell in (score.prediction_band_cell_id, *score.applicable_slice_cell_ids)
        ) | {"quantile-p50", "quantile-p90", "target-0.95"}
        eligible_cells = all_cells - {"target-0.95"}
        scoring_state = ScoringState.READY
        feed_status = FeedStatus.FRESH
        if request.origin_station_id == "alpha-stale":
            scoring_state = ScoringState.STALE
            feed_status = FeedStatus.STALE
        elif request.origin_station_id == "alpha-absent":
            scoring_state = ScoringState.ABSTAINED
            feed_status = FeedStatus.ABSENT
        elif request.origin_station_id == "alpha-sparse":
            eligible_cells = frozenset()
        slack = int(
            (request.effective_deadline_at_utc - request.effective_ready_at_utc).total_seconds()
            // 60
        )
        manifest_hash = hashlib.sha256(
            "|".join(score.itinerary.policy_key for score in scores).encode()
        ).hexdigest()
        return SearchMaterials(
            scores,
            DecisionContext(
                request.initial_query_cutoff_utc,
                "SYNTHETIC_BROWSER_FIXTURE",
                "ALERT_MASK_V1",
                manifest_hash,
                tuple((score.itinerary.policy_key, True) for score in scores),
            ),
            EligibilityManifest(
                all_cells,
                eligible_cells,
                (("0.95", ("target-0.95",)),),
            ),
            HorizonSupportManifest(frozenset({f"slack-{slack}"})),
            scoring_state,
            feed_status,
            "SYNTHETIC_BROWSER_MODEL_V1",
            "historical_v1",
            source_attempt_lineage=("SYNTHETIC_BROWSER_ATTEMPT",),
        )

    def recovery(self, request: RecoveryRequest) -> RecoveryMaterials:
        continuation = _recovery_candidate(request.recovery_cutoff_utc, "continuation", 20)
        recommendation = _recovery_candidate(request.recovery_cutoff_utc, "recommended", 14)
        backup = _recovery_candidate(request.recovery_cutoff_utc, "backup", 17)
        candidates = (continuation, recommendation, backup)
        return RecoveryMaterials(
            candidates,
            continuation.policy_key,
            DecisionContext(
                request.recovery_cutoff_utc,
                "SYNTHETIC_RECOVERY_FIXTURE",
                "ALERT_MASK_V1",
                hashlib.sha256(b"synthetic-recovery").hexdigest(),
                tuple((candidate.policy_key, True) for candidate in candidates),
            ),
            RecoveryTriggerInput(
                TripState.AT_TRANSFER,
                True,
                None,
                False,
                False,
                False,
                True,
                False,
            ),
        )


def _recovery_candidate(cutoff: datetime, suffix: str, arrival_minutes: int) -> CandidateItinerary:
    leg = TransitLeg(
        f"recovery-{suffix}",
        "Orange",
        1,
        f"recovery-trip-{suffix}",
        "park-orange",
        "park",
        "bravo-platform",
        "bravo",
        cutoff + timedelta(minutes=1),
        cutoff + timedelta(minutes=arrival_minutes),
        ("park-orange", "bravo-platform"),
    )
    return CandidateItinerary((leg,), ())
