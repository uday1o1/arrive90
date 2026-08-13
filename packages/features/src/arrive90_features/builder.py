"""Side-effect-free feature builder restricted to a frozen TemporalView."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from arrive90_data_contracts.candidates import CandidateItinerary, HistoricalBaseQuery
from arrive90_data_contracts.realtime import require_utc
from arrive90_ingestion.temporal import TemporalView

from arrive90_features.registry import HISTORICAL_V1_REGISTRY, FeatureRegistry

type FeatureValue = str | int | float | bool | None


@dataclass(frozen=True)
class FeaturePrimitive:
    feature_name: str
    policy_key: str | None
    value: FeatureValue
    source_attempt_id: str | None
    historical_source_row_key: str | None


@dataclass(frozen=True)
class FeatureRow:
    query_id: str
    itinerary_id: str
    feature_cutoff_utc: datetime
    feature_schema_version: str
    registry_manifest_hash: str
    values: tuple[tuple[str, FeatureValue], ...]
    source_attempt_ids: tuple[str, ...]
    historical_source_row_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        require_utc(self.feature_cutoff_utc, "feature_cutoff_utc")
        if tuple(sorted(self.values)) != self.values:
            raise ValueError("feature values must be sorted by name")
        if any("deadline" in name for name, _ in self.values):
            raise ValueError("deadline metadata cannot enter a feature row")


class FeatureBuilder:
    def __init__(
        self,
        registry: FeatureRegistry = HISTORICAL_V1_REGISTRY,
        *,
        agency_timezone: str = "America/New_York",
    ) -> None:
        self.registry = registry
        self.zone = ZoneInfo(agency_timezone)

    def build(
        self,
        query: HistoricalBaseQuery,
        candidate: CandidateItinerary,
        temporal_view: TemporalView[FeaturePrimitive],
    ) -> FeatureRow:
        if temporal_view.cutoff_utc != query.query_time_utc:
            raise ValueError("feature TemporalView cutoff must equal the query cutoff")
        local = query.query_time_utc.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        schedule_values: dict[str, FeatureValue] = {
            "day_of_week_cos": math.cos(2 * math.pi * local.weekday() / 7),
            "day_of_week_sin": math.sin(2 * math.pi * local.weekday() / 7),
            "direction_ids": ",".join(str(leg.direction_id) for leg in candidate.legs),
            "destination_station_id": candidate.legs[-1].alighting_parent_station_id,
            "origin_station_id": candidate.legs[0].boarding_parent_station_id,
            "route_ids": ",".join(leg.route_id for leg in candidate.legs),
            "scheduled_duration_seconds": candidate.planned_duration_seconds,
            "scheduled_leg_duration_seconds": ",".join(
                str(int((leg.scheduled_arrival_utc - leg.scheduled_departure_utc).total_seconds()))
                for leg in candidate.legs
            ),
            "scheduled_transfer_buffer_seconds": (
                int(
                    (
                        candidate.legs[1].scheduled_departure_utc
                        - candidate.legs[0].scheduled_arrival_utc
                    ).total_seconds()
                )
                if candidate.transfer_count
                else None
            ),
            "time_of_day_cos": math.cos(2 * math.pi * minute / 1440),
            "time_of_day_sin": math.sin(2 * math.pi * minute / 1440),
            "transfer_station_id": (
                candidate.legs[0].alighting_parent_station_id if candidate.transfer_count else None
            ),
        }
        for name in schedule_values:
            self.registry.require(name)
        attempts: set[str] = set()
        rows: set[str] = set()
        for record in temporal_view.available():
            primitive = record.value
            if primitive.policy_key not in (None, candidate.policy_key):
                continue
            spec = self.registry.require(primitive.feature_name)
            if spec.source.value == "STATIC_SCHEDULE":
                raise ValueError(
                    "static schedule features cannot be overridden by source primitives"
                )
            schedule_values[primitive.feature_name] = primitive.value
            if primitive.source_attempt_id is not None:
                attempts.add(primitive.source_attempt_id)
            if primitive.historical_source_row_key is not None:
                rows.add(primitive.historical_source_row_key)
        return FeatureRow(
            query_id=query.query_id,
            itinerary_id=candidate.policy_key,
            feature_cutoff_utc=query.query_time_utc,
            feature_schema_version=self.registry.version,
            registry_manifest_hash=self.registry.manifest_hash,
            values=tuple(sorted(schedule_values.items())),
            source_attempt_ids=tuple(sorted(attempts, key=str.encode)),
            historical_source_row_keys=tuple(sorted(rows, key=str.encode)),
        )
