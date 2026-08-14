"""Deterministic normalization for Bus Observatory VehiclePosition Parquet objects."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.travel_time import (
    SourceLineageEntry,
    VehicleObservation,
    decode_trip_schedule_relationship,
    decode_vehicle_status,
    vehicle_observation_id,
)

from arrive90_ingestion.acquisition import PARQUET_PARSER_VERSION, parquet_profile

RAIL_ROUTE_IDS = ("Blue", "Orange", "Red")
VEHICLE_SCHEMA_VERSION = "bus-observatory-vehicle-position-v1"

ENTITY_ID = "id"
TRIP_ID = "vehicle.trip.trip_id"
TRIP_START_TIME = "vehicle.trip.start_time"
TRIP_START_DATE = "vehicle.trip.start_date"
SCHEDULE_RELATIONSHIP = "vehicle.trip.schedule_relationship"
ROUTE_ID = "vehicle.trip.route_id"
DIRECTION_ID = "vehicle.trip.direction_id"
LATITUDE = "vehicle.position.latitude"
LONGITUDE = "vehicle.position.longitude"
BEARING = "vehicle.position.bearing"
STOP_SEQUENCE = "vehicle.current_stop_sequence"
CURRENT_STATUS = "vehicle.current_status"
OBSERVATION_TIMESTAMP = "vehicle.timestamp"
STOP_ID = "vehicle.stop_id"
VEHICLE_ID = "vehicle.vehicle.id"
VEHICLE_LABEL = "vehicle.vehicle.label"
SPEED = "vehicle.position.speed"

REQUIRED_COLUMNS = (
    ENTITY_ID,
    TRIP_ID,
    TRIP_START_TIME,
    TRIP_START_DATE,
    SCHEDULE_RELATIONSHIP,
    ROUTE_ID,
    DIRECTION_ID,
    LATITUDE,
    LONGITUDE,
    BEARING,
    STOP_SEQUENCE,
    CURRENT_STATUS,
    OBSERVATION_TIMESTAMP,
    STOP_ID,
    VEHICLE_ID,
    VEHICLE_LABEL,
    SPEED,
)

IDENTITY_AVAILABILITY_COLUMNS = (
    ROUTE_ID,
    TRIP_ID,
    DIRECTION_ID,
    VEHICLE_ID,
    CURRENT_STATUS,
    OBSERVATION_TIMESTAMP,
)

_STRING_COLUMNS = {
    ENTITY_ID,
    TRIP_ID,
    TRIP_START_TIME,
    TRIP_START_DATE,
    ROUTE_ID,
    STOP_ID,
    VEHICLE_ID,
    VEHICLE_LABEL,
}
_NUMERIC_COLUMNS = {
    SCHEDULE_RELATIONSHIP,
    DIRECTION_ID,
    LATITUDE,
    LONGITUDE,
    BEARING,
    STOP_SEQUENCE,
    CURRENT_STATUS,
    SPEED,
}


class VehicleNormalizationError(ValueError):
    """Raised when a source object cannot be normalized under the frozen schema."""


@dataclass(frozen=True, slots=True)
class QuarantinedVehicleRow:
    """One exact source row excluded with an explicit deterministic reason."""

    source_object_key: str
    source_row_ordinal: int
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class NormalizedVehicleDay:
    """Normalized observations and complete source-quality accounting."""

    source_object_key: str
    source_path: Path
    parser_version: str
    source_schema_fingerprint: str
    source_row_count: int
    retained_raw_row_count: int
    observations: tuple[VehicleObservation, ...]
    quarantined_rows: tuple[QuarantinedVehicleRow, ...]
    exact_duplicate_row_count: int
    conflicting_identity_count: int
    retained_rows_by_route: tuple[tuple[str, int], ...]
    identity_availability_overall: float
    identity_availability_by_route: tuple[tuple[str, float], ...]
    source_min_naive_utc: datetime
    source_max_naive_utc: datetime


def _validate_schema(schema: pa.Schema) -> None:
    fields = {field.name: field for field in schema}
    missing = sorted(set(REQUIRED_COLUMNS) - fields.keys())
    if missing:
        raise VehicleNormalizationError(f"source Parquet is missing required columns: {missing}")
    wrong: list[str] = []
    for name in REQUIRED_COLUMNS:
        data_type = fields[name].type
        invalid_type = (
            (name in _STRING_COLUMNS and not pa.types.is_string(data_type))
            or (
                name in _NUMERIC_COLUMNS
                and not (pa.types.is_floating(data_type) or pa.types.is_integer(data_type))
            )
            or (name == OBSERVATION_TIMESTAMP and not pa.types.is_timestamp(data_type))
        )
        if invalid_type:
            wrong.append(f"{name}={data_type}")
    if wrong:
        raise VehicleNormalizationError(f"source Parquet has incompatible column types: {wrong}")


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string or null")
    return value


def _integral(value: object, field: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be integral")
    numeric = float(value)
    if not numeric.is_integer():
        raise ValueError(f"{field} must be integral")
    return int(numeric)


def _optional_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be numeric or null")
    return float(value)


def _trip_date(value: object) -> date:
    text = _required_string(value, TRIP_START_DATE)
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as error:
        raise ValueError(f"{TRIP_START_DATE} must use YYYYMMDD") from error


def _source_timestamp(value: object) -> tuple[datetime, datetime]:
    if not isinstance(value, datetime):
        raise ValueError(f"{OBSERVATION_TIMESTAMP} must be a datetime")
    if value.tzinfo is not None:
        raise ValueError(f"{OBSERVATION_TIMESTAMP} must be timezone-naive UTC")
    return value, value.replace(tzinfo=UTC)


def _parse_observation(
    row: dict[str, Any],
    *,
    source_object_key: str,
    source_row_ordinal: int,
) -> VehicleObservation:
    trip_start_date = _trip_date(row[TRIP_START_DATE])
    trip_start_time = _required_string(row[TRIP_START_TIME], TRIP_START_TIME)
    trip_id = _required_string(row[TRIP_ID], TRIP_ID)
    route_id = _required_string(row[ROUTE_ID], ROUTE_ID)
    direction_id = _integral(row[DIRECTION_ID], DIRECTION_ID)
    vehicle_id = _required_string(row[VEHICLE_ID], VEHICLE_ID)
    stop_sequence = _integral(row[STOP_SEQUENCE], STOP_SEQUENCE, optional=True)
    current_status = decode_vehicle_status(row[CURRENT_STATUS])
    source_naive_utc, observation_utc = _source_timestamp(row[OBSERVATION_TIMESTAMP])
    if direction_id is None:
        raise ValueError(f"{DIRECTION_ID} is required")
    observation_id = vehicle_observation_id(
        trip_start_date=trip_start_date,
        trip_start_time=trip_start_time,
        trip_id=trip_id,
        route_id=route_id,
        direction_id=direction_id,
        vehicle_id=vehicle_id,
        observation_utc=observation_utc,
        stop_sequence=stop_sequence,
        current_status=current_status,
    )
    return VehicleObservation(
        observation_id=observation_id,
        source_lineage=(SourceLineageEntry(source_object_key, source_row_ordinal),),
        entity_id=_optional_string(row[ENTITY_ID], ENTITY_ID),
        trip_id=trip_id,
        trip_start_date=trip_start_date,
        trip_start_time=trip_start_time,
        schedule_relationship=decode_trip_schedule_relationship(row[SCHEDULE_RELATIONSHIP]),
        route_id=route_id,
        direction_id=direction_id,
        vehicle_id=vehicle_id,
        vehicle_label=_optional_string(row[VEHICLE_LABEL], VEHICLE_LABEL),
        observation_source_naive_utc=source_naive_utc,
        observation_utc=observation_utc,
        stop_sequence=stop_sequence,
        stop_id=_optional_string(row[STOP_ID], STOP_ID),
        current_status=current_status,
        latitude=_optional_float(row[LATITUDE], LATITUDE),
        longitude=_optional_float(row[LONGITUDE], LONGITUDE),
        bearing=_optional_float(row[BEARING], BEARING),
        speed=_optional_float(row[SPEED], SPEED),
        schema_version=VEHICLE_SCHEMA_VERSION,
    )


def _observation_sort_key(observation: VehicleObservation) -> tuple[object, ...]:
    return (
        observation.trip_start_date,
        observation.trip_start_time.encode(),
        observation.trip_id.encode(),
        observation.route_id.encode(),
        observation.direction_id,
        observation.vehicle_id.encode(),
        observation.observation_utc,
        observation.stop_sequence is None,
        observation.stop_sequence if observation.stop_sequence is not None else 0,
        observation.current_status.value.encode(),
        observation.observation_id,
    )


def _availability(
    table: pa.Table, indices: pa.Array
) -> tuple[float, tuple[tuple[str, float], ...]]:
    retained = table.take(indices)
    if retained.num_rows == 0:
        raise VehicleNormalizationError("source Parquet contains no retained heavy-rail rows")
    complete = pc.invert(pc.is_null(retained[IDENTITY_AVAILABILITY_COLUMNS[0]]))
    for name in IDENTITY_AVAILABILITY_COLUMNS[1:]:
        complete = pc.and_kleene(complete, pc.invert(pc.is_null(retained[name])))
    overall = pc.sum(pc.cast(pc.fill_null(complete, False), pa.int64())).as_py()
    by_route: list[tuple[str, float]] = []
    for route_id in RAIL_ROUTE_IDS:
        mask = pc.equal(retained[ROUTE_ID], route_id)
        denominator = pc.sum(pc.cast(mask, pa.int64())).as_py()
        route_complete = pc.and_kleene(complete, mask)
        numerator = pc.sum(pc.cast(pc.fill_null(route_complete, False), pa.int64())).as_py()
        by_route.append((route_id, float(numerator / denominator) if denominator else 0.0))
    return float(overall / retained.num_rows), tuple(by_route)


def normalize_vehicle_parquet(path: Path, *, source_object_key: str) -> NormalizedVehicleDay:
    """Normalize one complete source object with deterministic lineage and quarantine."""

    parquet = pq.ParquetFile(path)
    _validate_schema(parquet.schema_arrow)
    table = parquet.read(columns=list(REQUIRED_COLUMNS))
    rail_mask = pc.is_in(table[ROUTE_ID], value_set=pa.array(RAIL_ROUTE_IDS))
    retained_indices = pc.indices_nonzero(pc.fill_null(rail_mask, False))
    retained = table.take(retained_indices)
    if retained.num_rows == 0:
        raise VehicleNormalizationError("source Parquet contains no retained heavy-rail rows")
    row_ordinals = retained_indices.to_pylist()
    source_times = retained[OBSERVATION_TIMESTAMP]
    minimum = pc.min(source_times).as_py()
    maximum = pc.max(source_times).as_py()
    if not isinstance(minimum, datetime) or not isinstance(maximum, datetime):
        raise VehicleNormalizationError("retained source timestamps are empty or invalid")

    parsed: list[VehicleObservation] = []
    quarantined: list[QuarantinedVehicleRow] = []
    for ordinal, row in zip(row_ordinals, retained.to_pylist(), strict=True):
        try:
            parsed.append(
                _parse_observation(
                    row,
                    source_object_key=source_object_key,
                    source_row_ordinal=int(ordinal),
                )
            )
        except (TypeError, ValueError) as error:
            quarantined.append(
                QuarantinedVehicleRow(
                    source_object_key=source_object_key,
                    source_row_ordinal=int(ordinal),
                    reason="INVALID_SOURCE_ROW",
                    detail=str(error),
                )
            )

    by_identity: dict[str, list[VehicleObservation]] = defaultdict(list)
    for observation in parsed:
        by_identity[observation.observation_id].append(observation)
    observations: list[VehicleObservation] = []
    exact_duplicate_rows = 0
    conflicting_identities = 0
    for observation_id in sorted(by_identity):
        matches = by_identity[observation_id]
        payloads = {observation.canonical_state_payload for observation in matches}
        if len(payloads) != 1:
            conflicting_identities += 1
            quarantined.extend(
                QuarantinedVehicleRow(
                    source_object_key=lineage.source_object_key,
                    source_row_ordinal=lineage.source_row_ordinal,
                    reason="CONFLICTING_DUPLICATE_STATE",
                    detail=(f"canonical identity {observation_id} has multiple state payloads"),
                )
                for observation in matches
                for lineage in observation.source_lineage
            )
            continue
        lineage = tuple(
            sorted(
                {source for observation in matches for source in observation.source_lineage},
                key=lambda item: (item.source_object_key.encode(), item.source_row_ordinal),
            )
        )
        exact_duplicate_rows += len(matches) - 1
        observations.append(replace(matches[0], source_lineage=lineage))

    observations.sort(key=_observation_sort_key)
    quarantined.sort(
        key=lambda item: (
            item.source_object_key.encode(),
            item.source_row_ordinal,
            item.reason.encode(),
        )
    )
    retained_counts = Counter(
        str(route_id) for route_id in retained[ROUTE_ID].to_pylist() if route_id is not None
    )
    availability_overall, availability_by_route = _availability(table, retained_indices)
    profile = parquet_profile(path)
    return NormalizedVehicleDay(
        source_object_key=source_object_key,
        source_path=path,
        parser_version=PARQUET_PARSER_VERSION,
        source_schema_fingerprint=profile.schema_fingerprint,
        source_row_count=profile.row_count,
        retained_raw_row_count=retained.num_rows,
        observations=tuple(observations),
        quarantined_rows=tuple(quarantined),
        exact_duplicate_row_count=exact_duplicate_rows,
        conflicting_identity_count=conflicting_identities,
        retained_rows_by_route=tuple(
            (route_id, retained_counts[route_id]) for route_id in RAIL_ROUTE_IDS
        ),
        identity_availability_overall=availability_overall,
        identity_availability_by_route=availability_by_route,
        source_min_naive_utc=minimum,
        source_max_naive_utc=maximum,
    )
