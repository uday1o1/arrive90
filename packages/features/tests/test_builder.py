from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from arrive90_data_contracts.candidates import CandidateItinerary, HistoricalBaseQuery, TransitLeg
from arrive90_features.builder import FeatureBuilder, FeaturePrimitive, FeatureRow
from arrive90_ingestion.temporal import TemporalRecord, TemporalView

NOW = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _query() -> HistoricalBaseQuery:
    return HistoricalBaseQuery(
        "query",
        NOW,
        date(2025, 1, 1),
        "station-a",
        "station-b",
        NOW,
        NOW + timedelta(minutes=210),
        "schedule",
        "v1",
        "Red",
        1.0,
        "train",
    )


def _candidate() -> CandidateItinerary:
    leg = TransitLeg(
        "pattern",
        "Red",
        0,
        "trip",
        "stop-a",
        "station-a",
        "stop-b",
        "station-b",
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=11),
        ("stop-a", "stop-b"),
    )
    return CandidateItinerary((leg,), ())


def test_schedule_feature_builder_is_deterministic_and_has_no_deadline() -> None:
    builder = FeatureBuilder()
    query = _query()
    candidate = _candidate()
    first = builder.build(query, candidate, TemporalView((), NOW))
    second = builder.build(query, candidate, TemporalView((), NOW))
    assert first == second
    values = dict(first.values)
    assert values["scheduled_duration_seconds"] == 600
    assert values["route_ids"] == "Red"
    assert all("deadline" not in name for name in values)
    assert first.source_attempt_ids == ()


def test_direct_and_indirect_future_feature_access_fail_through_real_builder() -> None:
    builder = FeatureBuilder()
    query = _query()
    candidate = _candidate()
    future = TemporalRecord(
        "future-static-override",
        NOW,
        NOW + timedelta(seconds=1),
        FeaturePrimitive(
            "scheduled_duration_seconds",
            candidate.policy_key,
            1,
            "future-attempt",
            "future-row",
        ),
    )
    row = builder.build(query, candidate, TemporalView((future,), NOW))
    assert dict(row.values)["scheduled_duration_seconds"] == 600
    with pytest.raises(ValueError, match="TemporalView cutoff"):
        builder.build(query, candidate, TemporalView((), NOW + timedelta(seconds=1)))


def test_unknown_or_static_override_primitive_is_rejected_when_available() -> None:
    query = _query()
    candidate = _candidate()
    unknown = TemporalRecord(
        "unknown",
        NOW,
        NOW,
        FeaturePrimitive("future_realized_headway", None, 1.0, None, "row"),
    )
    with pytest.raises(ValueError, match="not registered"):
        FeatureBuilder().build(query, candidate, TemporalView((unknown,), NOW))
    override = TemporalRecord(
        "override",
        NOW,
        NOW,
        FeaturePrimitive("scheduled_duration_seconds", None, 1, None, "row"),
    )
    with pytest.raises(ValueError, match="cannot be overridden"):
        FeatureBuilder().build(query, candidate, TemporalView((override,), NOW))


def test_feature_row_rejects_deadline_metadata_and_unsorted_values() -> None:
    with pytest.raises(ValueError, match="sorted"):
        FeatureRow("q", "i", NOW, "v", "h", (("z", 1), ("a", 2)), (), ())
    with pytest.raises(ValueError, match="deadline"):
        FeatureRow("q", "i", NOW, "v", "h", (("deadline_slack", 5),), (), ())
