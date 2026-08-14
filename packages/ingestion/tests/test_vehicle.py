from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_ingestion.vehicle import (
    BEARING,
    CURRENT_STATUS,
    DIRECTION_ID,
    ENTITY_ID,
    LATITUDE,
    LONGITUDE,
    OBSERVATION_TIMESTAMP,
    ROUTE_ID,
    SCHEDULE_RELATIONSHIP,
    SPEED,
    STOP_ID,
    STOP_SEQUENCE,
    TRIP_ID,
    TRIP_START_DATE,
    TRIP_START_TIME,
    VEHICLE_ID,
    VEHICLE_LABEL,
    VehicleNormalizationError,
    normalize_vehicle_parquet,
)

SOURCE_KEY = "feeds/mbta_all/day.parquet"


def _row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        ENTITY_ID: "entity-1",
        TRIP_ID: "trip-1",
        TRIP_START_TIME: "08:00:00",
        TRIP_START_DATE: "20240515",
        SCHEDULE_RELATIONSHIP: 0.0,
        ROUTE_ID: "Red",
        DIRECTION_ID: 0.0,
        LATITUDE: 42.35,
        LONGITUDE: -71.06,
        BEARING: 90.0,
        STOP_SEQUENCE: 10.0,
        CURRENT_STATUS: 1.0,
        OBSERVATION_TIMESTAMP: datetime(2024, 5, 15, 12, 0),
        STOP_ID: "70001",
        VEHICLE_ID: "vehicle-1",
        VEHICLE_LABEL: "train-1",
        SPEED: 0.0,
    }
    row.update(changes)
    return row


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_normalizer_filters_rail_attaches_utc_and_collapses_exact_duplicates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "day.parquet"
    first = _row()
    _write(
        path,
        [
            first,
            dict(first),
            _row(
                **{
                    ENTITY_ID: "bus-entity",
                    ROUTE_ID: "1",
                    TRIP_ID: "bus-trip",
                    VEHICLE_ID: "bus-1",
                }
            ),
        ],
    )

    normalized = normalize_vehicle_parquet(path, source_object_key=SOURCE_KEY)
    repeated = normalize_vehicle_parquet(path, source_object_key=SOURCE_KEY)

    assert normalized == repeated
    assert normalized.source_row_count == 3
    assert normalized.retained_raw_row_count == 2
    assert normalized.exact_duplicate_row_count == 1
    assert normalized.conflicting_identity_count == 0
    assert len(normalized.observations) == 1
    observation = normalized.observations[0]
    assert observation.observation_source_naive_utc == datetime(2024, 5, 15, 12)
    assert observation.observation_utc == datetime(2024, 5, 15, 12, tzinfo=UTC)
    assert tuple(item.source_row_ordinal for item in observation.source_lineage) == (0, 1)
    assert normalized.retained_rows_by_route == (("Blue", 0), ("Orange", 0), ("Red", 2))
    assert normalized.identity_availability_overall == 1.0
    assert normalized.source_min_naive_utc == datetime(2024, 5, 15, 12)


def test_normalizer_quarantines_conflicts_invalid_enums_and_missing_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.parquet"
    _write(
        path,
        [
            _row(),
            _row(**{VEHICLE_LABEL: "different-state"}),
            _row(
                **{
                    ENTITY_ID: "entity-2",
                    TRIP_ID: None,
                    VEHICLE_ID: "vehicle-2",
                    OBSERVATION_TIMESTAMP: datetime(2024, 5, 15, 12, 1),
                }
            ),
            _row(
                **{
                    ENTITY_ID: "entity-3",
                    TRIP_ID: "trip-3",
                    VEHICLE_ID: "vehicle-3",
                    CURRENT_STATUS: 99.0,
                    OBSERVATION_TIMESTAMP: datetime(2024, 5, 15, 12, 2),
                }
            ),
        ],
    )

    normalized = normalize_vehicle_parquet(path, source_object_key=SOURCE_KEY)

    assert normalized.observations == ()
    assert normalized.conflicting_identity_count == 1
    assert [row.reason for row in normalized.quarantined_rows] == [
        "CONFLICTING_DUPLICATE_STATE",
        "CONFLICTING_DUPLICATE_STATE",
        "INVALID_SOURCE_ROW",
        "INVALID_SOURCE_ROW",
    ]
    assert normalized.identity_availability_overall == 0.75
    assert dict(normalized.identity_availability_by_route)["Red"] == 0.75


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({DIRECTION_ID: 0.5}, "must be integral"),
        ({STOP_SEQUENCE: 10.5}, "must be integral"),
        ({SCHEDULE_RELATIONSHIP: 4.0}, "unknown trip schedule"),
        ({LATITUDE: 91.0}, "latitude"),
        ({TRIP_START_DATE: "2024-05-15"}, "YYYYMMDD"),
        ({TRIP_START_TIME: "8am"}, "GTFS"),
    ],
)
def test_normalizer_quarantines_invalid_row_values(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    path = tmp_path / "invalid-value.parquet"
    _write(path, [_row(**changes)])
    normalized = normalize_vehicle_parquet(path, source_object_key=SOURCE_KEY)
    assert normalized.observations == ()
    assert message in normalized.quarantined_rows[0].detail


def test_normalizer_rejects_missing_or_incompatible_physical_schema(tmp_path: Path) -> None:
    missing = tmp_path / "missing.parquet"
    pq.write_table(pa.table({TRIP_ID: ["trip-1"]}), missing)
    with pytest.raises(VehicleNormalizationError, match="missing required columns"):
        normalize_vehicle_parquet(missing, source_object_key=SOURCE_KEY)

    wrong = tmp_path / "wrong.parquet"
    row = _row()
    table = pa.Table.from_pylist([row]).set_column(
        len(row) - 1,
        SPEED,
        pa.array(["not-numeric"]),
    )
    pq.write_table(table, wrong)
    with pytest.raises(VehicleNormalizationError, match="incompatible column types"):
        normalize_vehicle_parquet(wrong, source_object_key=SOURCE_KEY)


def test_normalizer_rejects_source_without_retained_rail_rows(tmp_path: Path) -> None:
    path = tmp_path / "bus-only.parquet"
    _write(path, [_row(**{ROUTE_ID: "1"})])
    with pytest.raises(VehicleNormalizationError, match="no retained heavy-rail"):
        normalize_vehicle_parquet(path, source_object_key=SOURCE_KEY)
