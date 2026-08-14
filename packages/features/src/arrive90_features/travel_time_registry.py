"""Frozen point-in-observation feature registry for travel-time-v1."""

from __future__ import annotations

from arrive90_features.registry import FeatureRegistry, FeatureSource, FeatureSpec


def _feature(
    name: str,
    value_type: str,
    source: FeatureSource,
    *,
    units: str | None = None,
    default: str = "required",
) -> FeatureSpec:
    return FeatureSpec(
        name=name,
        owner="travel_time",
        value_type=value_type,
        units=units,
        source=source,
        event_time_rule="value is evaluated at or before the anchor observation cutoff",
        product_availability_rule=(
            "schedule publication and source observation time cannot exceed the anchor cutoff"
        ),
        online_equivalent_derivation="shared deterministic observation-cutoff feature builder",
        default_behavior=default,
        seeded_leakage_fixture=f"travel-time-{name}-cutoff",
    )


TRAVEL_TIME_V1_SPECS = (
    _feature("route_id", "categorical", FeatureSource.STATIC_SCHEDULE),
    _feature("direction_id", "categorical", FeatureSource.STATIC_SCHEDULE),
    _feature("origin_stop_id", "categorical", FeatureSource.STATIC_SCHEDULE),
    _feature("destination_stop_id", "categorical", FeatureSource.STATIC_SCHEDULE),
    _feature("route_pattern_id", "categorical", FeatureSource.STATIC_SCHEDULE),
    _feature("origin_stop_sequence", "integer", FeatureSource.STATIC_SCHEDULE),
    _feature("destination_stop_sequence", "integer", FeatureSource.STATIC_SCHEDULE),
    _feature("remaining_scheduled_stop_count", "integer", FeatureSource.STATIC_SCHEDULE),
    _feature(
        "scheduled_remaining_seconds", "float", FeatureSource.STATIC_SCHEDULE, units="seconds"
    ),
    _feature(
        "observed_origin_lateness_seconds",
        "float",
        FeatureSource.HISTORICAL_DERIVED,
        units="seconds",
    ),
    _feature("scheduled_progress_fraction", "float", FeatureSource.STATIC_SCHEDULE),
    _feature("local_time_sin", "float", FeatureSource.HISTORICAL_DERIVED),
    _feature("local_time_cos", "float", FeatureSource.HISTORICAL_DERIVED),
    _feature("day_of_week_sin", "float", FeatureSource.HISTORICAL_DERIVED),
    _feature("day_of_week_cos", "float", FeatureSource.HISTORICAL_DERIVED),
    _feature("weekend", "boolean", FeatureSource.HISTORICAL_DERIVED),
    _feature("trip_start_hour", "integer", FeatureSource.STATIC_SCHEDULE),
    _feature(
        "elapsed_episode_seconds",
        "float",
        FeatureSource.HISTORICAL_DERIVED,
        units="seconds",
    ),
    _feature("observed_stops_before_anchor", "integer", FeatureSource.HISTORICAL_DERIVED),
    _feature(
        "previous_stopped_segment_seconds",
        "float_or_null",
        FeatureSource.HISTORICAL_DERIVED,
        units="seconds",
        default="zero with an explicit missingness indicator",
    ),
    _feature(
        "previous_stopped_segment_seconds_missing",
        "boolean",
        FeatureSource.HISTORICAL_DERIVED,
    ),
    _feature(
        "median_last_three_segment_seconds",
        "float_or_null",
        FeatureSource.HISTORICAL_DERIVED,
        units="seconds",
        default="zero with an explicit missingness indicator",
    ),
    _feature(
        "median_last_three_segment_seconds_missing",
        "boolean",
        FeatureSource.HISTORICAL_DERIVED,
    ),
    _feature(
        "most_recent_observation_gap_seconds",
        "float_or_null",
        FeatureSource.HISTORICAL_DERIVED,
        units="seconds",
        default="zero with an explicit missingness indicator",
    ),
    _feature(
        "most_recent_observation_gap_seconds_missing",
        "boolean",
        FeatureSource.HISTORICAL_DERIVED,
    ),
    _feature(
        "anchor_latitude",
        "float_or_null",
        FeatureSource.VEHICLE_POSITION,
        units="degrees",
        default="zero with an explicit missingness indicator",
    ),
    _feature("anchor_latitude_missing", "boolean", FeatureSource.VEHICLE_POSITION),
    _feature(
        "anchor_longitude",
        "float_or_null",
        FeatureSource.VEHICLE_POSITION,
        units="degrees",
        default="zero with an explicit missingness indicator",
    ),
    _feature("anchor_longitude_missing", "boolean", FeatureSource.VEHICLE_POSITION),
    _feature(
        "anchor_bearing",
        "float_or_null",
        FeatureSource.VEHICLE_POSITION,
        units="degrees",
        default="zero with an explicit missingness indicator",
    ),
    _feature("anchor_bearing_missing", "boolean", FeatureSource.VEHICLE_POSITION),
    _feature(
        "anchor_speed",
        "float_or_null",
        FeatureSource.VEHICLE_POSITION,
        units="meters_per_second",
        default="zero with an explicit missingness indicator",
    ),
    _feature("anchor_speed_missing", "boolean", FeatureSource.VEHICLE_POSITION),
)

TRAVEL_TIME_V1_REGISTRY = FeatureRegistry("travel-time-v1", TRAVEL_TIME_V1_SPECS)
CATEGORICAL_FEATURES = tuple(
    spec.name for spec in TRAVEL_TIME_V1_SPECS if spec.value_type == "categorical"
)
NUMERIC_FEATURES = tuple(
    spec.name for spec in TRAVEL_TIME_V1_SPECS if spec.value_type != "categorical"
)
