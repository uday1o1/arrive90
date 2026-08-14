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
