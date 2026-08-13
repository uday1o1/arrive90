"""Versioned feature definitions and forbidden-family validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class FeatureSource(StrEnum):
    STATIC_SCHEDULE = "STATIC_SCHEDULE"
    VEHICLE_POSITION = "VEHICLE_POSITION"
    ALERT = "ALERT"
    FEED_ATTEMPT = "FEED_ATTEMPT"
    HISTORICAL_DERIVED = "HISTORICAL_DERIVED"
    TRIP_UPDATE_PREDICTION = "TRIP_UPDATE_PREDICTION"
    OUTCOME = "OUTCOME"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    owner: str
    value_type: str
    units: str | None
    source: FeatureSource
    event_time_rule: str
    product_availability_rule: str
    online_equivalent_derivation: str
    default_behavior: str
    seeded_leakage_fixture: str


class FeatureRegistry:
    def __init__(self, version: str, specs: tuple[FeatureSpec, ...]) -> None:
        if len({spec.name for spec in specs}) != len(specs):
            raise ValueError("feature registry contains duplicate names")
        forbidden = {
            FeatureSource.TRIP_UPDATE_PREDICTION,
            FeatureSource.OUTCOME,
        }
        invalid = sorted(spec.name for spec in specs if spec.source in forbidden)
        if invalid:
            raise ValueError(f"historical feature registry contains forbidden sources: {invalid}")
        self.version = version
        self.specs = {spec.name: spec for spec in specs}

    @property
    def manifest_hash(self) -> str:
        payload = json.dumps(
            {
                "specs": [asdict(self.specs[name]) for name in sorted(self.specs)],
                "version": self.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def require(self, name: str) -> FeatureSpec:
        try:
            return self.specs[name]
        except KeyError as error:
            raise ValueError(f"feature is not registered: {name}") from error


def _schedule(name: str, value_type: str, units: str | None = None) -> FeatureSpec:
    return FeatureSpec(
        name=name,
        owner="routing",
        value_type=value_type,
        units=units,
        source=FeatureSource.STATIC_SCHEDULE,
        event_time_rule="selected schedule version and canonical simulation",
        product_availability_rule="schedule known_at_utc must not exceed feature cutoff",
        online_equivalent_derivation="shared canonical schedule simulation",
        default_behavior="required",
        seeded_leakage_fixture=f"schedule-{name}-cutoff",
    )


HISTORICAL_V1_REGISTRY = FeatureRegistry(
    "historical_v1",
    (
        _schedule("day_of_week_cos", "float"),
        _schedule("day_of_week_sin", "float"),
        _schedule("direction_ids", "string"),
        _schedule("destination_station_id", "string"),
        _schedule("origin_station_id", "string"),
        _schedule("route_ids", "string"),
        _schedule("scheduled_duration_seconds", "integer", "seconds"),
        _schedule("scheduled_leg_duration_seconds", "string", "seconds"),
        _schedule("scheduled_transfer_buffer_seconds", "integer_or_null", "seconds"),
        _schedule("time_of_day_cos", "float"),
        _schedule("time_of_day_sin", "float"),
        _schedule("transfer_station_id", "string_or_null"),
    ),
)
