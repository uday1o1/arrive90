from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arrive90_service.browser_fixture import BrowserFixtureBackend
from arrive90_service.contracts import NormalizedJourneyRequest

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _request(**changes: object) -> NormalizedJourneyRequest:
    values: dict[str, object] = {
        "origin_station_id": "alpha",
        "destination_station_id": "bravo",
        "requested_ready_at_utc": NOW,
        "effective_ready_at_utc": NOW,
        "requested_deadline_at_utc": NOW + timedelta(minutes=30),
        "effective_deadline_at_utc": NOW + timedelta(minutes=30),
        "reliability_target": Decimal("0.90"),
        "maximum_extra_minutes": 20,
        "initial_query_cutoff_utc": NOW,
        "ready_time_status": "AS_REQUESTED",
        "deadline_time_status": "AS_REQUESTED",
        "limitations": (),
    }
    values.update(changes)
    return NormalizedJourneyRequest(**values)  # type: ignore[arg-type]


def test_browser_fixture_is_explicitly_synthetic_and_exercises_feed_states() -> None:
    backend = BrowserFixtureBackend()
    assert any("fixture" in station.name for station in backend.stations())
    fresh = backend.search(_request())
    assert fresh.model_version == "SYNTHETIC_BROWSER_MODEL_V1"
    assert fresh.source_attempt_lineage == ("SYNTHETIC_BROWSER_ATTEMPT",)
    assert not fresh.eligibility_manifest.cell_is_eligible("target-0.95")
    stale = backend.search(_request(origin_station_id="alpha-stale"))
    absent = backend.search(_request(origin_station_id="alpha-absent"))
    sparse = backend.search(_request(origin_station_id="alpha-sparse"))
    assert stale.scoring_state.value == "STALE"
    assert stale.feed_status.value == "STALE"
    assert absent.scoring_state.value == "ABSTAINED"
    assert absent.feed_status.value == "ABSENT"
    assert not sparse.eligibility_manifest.eligible_cells
