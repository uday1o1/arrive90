"""Outcome-sealed final feature inventory and Milestone 4 outcome opening."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_data_contracts.travel_time import DownstreamOutcomeState
from arrive90_features.transform import FeatureTransformInput, FeatureValue
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointQuery, three_hour_bucket
from scipy import sparse  # type: ignore[import-untyped]

from arrive90_evaluation.model_population import FEATURE_SCHEMA, SELECTED_SCHEMA
from arrive90_evaluation.modeling_data import ModelingContext
from arrive90_evaluation.year_dataset import YearDatasetError, read_outcome_partition

BOSTON = ZoneInfo("America/New_York")
FINAL_START = date(2024, 11, 1)
FINAL_END = date(2024, 12, 31)
FINITE_UPPER_STATES = frozenset(
    {
        DownstreamOutcomeState.INTERVAL_RESOLVED.value,
        DownstreamOutcomeState.LEFT_CENSORED.value,
        DownstreamOutcomeState.OVER_WIDTH_INTERVAL.value,
    }
)
LIKELIHOOD_STATES = frozenset(
    {
        DownstreamOutcomeState.INTERVAL_RESOLVED.value,
        DownstreamOutcomeState.LEFT_CENSORED.value,
        DownstreamOutcomeState.RIGHT_CENSORED.value,
    }
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _digest_bound(value: float) -> float | str | None:
    if math.isnan(value):
        return None
    if math.isinf(value):
        return "POSITIVE_INFINITY" if value > 0 else "NEGATIVE_INFINITY"
    return value


def _verify_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise YearDatasetError(f"{label} must be a SHA-256 digest")


def _verified_path(root: Path, entry: dict[str, Any], label: str) -> Path:
    path = root / str(entry.get("path", ""))
    if not path.is_file():
        raise YearDatasetError(f"{label} is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != entry.get("sha256"):
        raise YearDatasetError(f"{label} failed content verification: {path}")
    return path


def _feature_values(raw: dict[str, Any]) -> tuple[tuple[str, FeatureValue], ...]:
    return tuple((name, cast(FeatureValue, raw[name])) for name in TRAVEL_TIME_V1_REGISTRY.specs)


def _scheduled_bucket(seconds: float) -> str:
    if 0 < seconds <= 600:
        return "SHORT"
    if seconds <= 1_200:
        return "MEDIUM"
    if seconds <= 1_800:
        return "LONG"
    raise YearDatasetError("final scheduled duration is outside the frozen scope")


def _deviation_bucket(seconds: float) -> str:
    absolute = abs(seconds)
    if absolute <= 60:
        return "LOW"
    if absolute <= 300:
        return "TYPICAL"
    return "HIGH"


def _gap_bucket(value: FeatureValue) -> str:
    if value is None:
        return "MISSING"
    seconds = float(cast(int | float, value))
    if 0 <= seconds <= 75:
        return "LOW"
    if seconds <= 180:
        return "TYPICAL"
    if seconds <= 600:
        return "HIGH"
    raise YearDatasetError("final observation gap is outside the frozen episode contract")


def _season(month: int) -> str:
    if month in {12, 1, 2}:
        return "WINTER"
    if month in {3, 4, 5}:
        return "SPRING"
    if month in {6, 7, 8}:
        return "SUMMER"
    return "FALL"


@dataclass(frozen=True, slots=True)
class FinalFeatureRow:
    """Outcome-free metadata and raw registered features for one final example."""

    example_id: str
    source_example_sha256: str
    anchor_id: str
    service_date: date
    analysis_weight: float
    query: EmpiricalMidpointQuery
    feature_values: tuple[tuple[str, FeatureValue], ...]
    slices: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.example_id or not self.anchor_id or self.analysis_weight <= 0:
            raise ValueError("final feature row identity and weight are invalid")
        _verify_sha256(self.source_example_sha256, "source example hash")
        if self.slices != tuple(sorted(self.slices, key=lambda item: item[0].encode())):
            raise ValueError("final feature slices must use canonical order")

    @property
    def values(self) -> dict[str, FeatureValue]:
        return dict(self.feature_values)

    @property
    def slice_values(self) -> dict[str, str]:
        return dict(self.slices)


@dataclass(frozen=True, slots=True)
class FinalFeatureInventory:
    """Verified final feature rows loaded before any final outcome value is opened."""

    context: ModelingContext
    features: sparse.csr_matrix
    rows: tuple[FinalFeatureRow, ...]
    service_dates: tuple[str, ...]
    row_manifest_sha256: str
    final_test_outcomes_opened: bool = False

    def __post_init__(self) -> None:
        if self.final_test_outcomes_opened:
            raise ValueError("feature inventory cannot contain final outcomes")
        if self.features.shape != (
            len(self.rows),
            len(self.context.feature_transform.column_names),
        ):
            raise ValueError("final feature inventory matrix does not align with its rows")
        if len({row.example_id for row in self.rows}) != len(self.rows):
            raise ValueError("final feature inventory example identifiers must be unique")


@dataclass(frozen=True, slots=True)
class FinalTestAccess:
    """Hash-bound permission issued only after protocol and replay selection freeze."""

    protocol_sha256: str
    replay_selection_sha256: str
    requesting_milestone: int = 4

    def __post_init__(self) -> None:
        _verify_sha256(self.protocol_sha256, "evaluation protocol hash")
        _verify_sha256(self.replay_selection_sha256, "replay selection hash")
        if self.requesting_milestone != 4:
            raise ValueError("final-test access is restricted to Milestone 4")


@dataclass(frozen=True, slots=True)
class FinalEvaluationData:
    """Final features joined to outcomes after the frozen Milestone 4 access point."""

    inventory: FinalFeatureInventory
    outcome_states: tuple[str, ...]
    lower_bounds: np.ndarray
    upper_bounds: np.ndarray
    outcome_manifest_sha256: str
    access: FinalTestAccess

    def __post_init__(self) -> None:
        row_count = len(self.inventory.rows)
        if any(
            len(values) != row_count
            for values in (self.outcome_states, self.lower_bounds, self.upper_bounds)
        ):
            raise ValueError("final outcomes do not align with the frozen feature inventory")
        if any(
            state not in {item.value for item in DownstreamOutcomeState}
            for state in self.outcome_states
        ):
            raise ValueError("final outcomes contain an unknown state")

    @property
    def analysis_weights(self) -> np.ndarray:
        return np.asarray([row.analysis_weight for row in self.inventory.rows], dtype=np.float64)

    @property
    def likelihood_mask(self) -> np.ndarray:
        return np.asarray(
            [state in LIKELIHOOD_STATES for state in self.outcome_states], dtype=np.bool_
        )

    @property
    def finite_upper_mask(self) -> np.ndarray:
        return np.asarray(
            [state in FINITE_UPPER_STATES for state in self.outcome_states], dtype=np.bool_
        )


def load_final_feature_inventory(context: ModelingContext) -> FinalFeatureInventory:
    """Load and transform all final rows without reading an outcome partition."""

    feature_entries = {
        str(entry["service_date"]): entry
        for entry in context.population_manifest["feature_partitions"]
        if entry["split"] == DatasetSplit.FINAL_TEST.value
    }
    selection_entries = {
        str(entry["service_date"]): entry
        for entry in context.population_manifest["selection_partitions"]
        if entry["split"] == DatasetSplit.FINAL_TEST.value
    }
    if set(feature_entries) != set(selection_entries) or len(feature_entries) != 61:
        raise YearDatasetError("final feature and selection partition inventories do not align")
    expected_dates = {
        date.fromordinal(ordinal).isoformat()
        for ordinal in range(FINAL_START.toordinal(), FINAL_END.toordinal() + 1)
    }
    if set(feature_entries) != expected_dates:
        raise YearDatasetError("final feature inventory does not cover the frozen date range")
    matrices: list[sparse.csr_matrix] = []
    rows: list[FinalFeatureRow] = []
    digest = hashlib.sha256()
    for service_date_text in sorted(feature_entries):
        feature_path = _verified_path(
            context.dataset_root, feature_entries[service_date_text], "final feature partition"
        )
        selection_path = _verified_path(
            context.dataset_root,
            selection_entries[service_date_text],
            "final selection partition",
        )
        feature_rows = pq.read_table(feature_path, schema=FEATURE_SCHEMA).to_pylist()
        selection_rows = pq.read_table(selection_path, schema=SELECTED_SCHEMA).to_pylist()
        selection_by_id = {str(row["example_id"]): row for row in selection_rows}
        feature_ids = [str(row["example_id"]) for row in feature_rows]
        if (
            len(selection_by_id) != len(selection_rows)
            or len(set(feature_ids)) != len(feature_ids)
            or set(feature_ids) != set(selection_by_id)
        ):
            raise YearDatasetError("final feature and candidate rows do not align by example ID")
        inputs: list[FeatureTransformInput] = []
        service_date = date.fromisoformat(service_date_text)
        for feature in feature_rows:
            example_id = str(feature["example_id"])
            selection = selection_by_id[example_id]
            cutoff = selection["feature_cutoff_utc"]
            if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
                raise YearDatasetError("final feature cutoff must be timezone aware")
            values = _feature_values(feature)
            value_map = dict(values)
            local_hour = cutoff.astimezone(BOSTON).hour
            scheduled = float(selection["scheduled_remaining_seconds"])
            direction = str(selection["direction_id"])
            query = EmpiricalMidpointQuery(
                anchor_id=str(feature["anchor_observation_id"]),
                route_id=str(selection["route_id"]),
                direction_id=direction,
                origin_stop_id=str(feature["origin_stop_id"]),
                destination_stop_id=str(selection["destination_stop_id"]),
                destination_offset=int(selection["destination_offset"]),
                day_type="WEEKDAY" if service_date.isoweekday() <= 5 else "WEEKEND",
                time_bucket=three_hour_bucket(local_hour),
            )
            slices = {
                "anchor_schedule_deviation_bucket": _deviation_bucket(
                    float(cast(int | float, value_map["observed_origin_lateness_seconds"]))
                ),
                "day_type": query.day_type,
                "destination_class": str(selection["destination_class"]),
                "line_direction": f"{selection['route_id']}|{direction}",
                "month": service_date.strftime("%Y-%m"),
                "observation_gap_bucket": _gap_bucket(
                    value_map["most_recent_observation_gap_seconds"]
                ),
                "peak_period": str(selection["peak_period"]),
                "platform_match_status": "EXACT",
                "scheduled_remaining_bucket": _scheduled_bucket(scheduled),
                "season": _season(service_date.month),
                "stop_sequence_match_status": "EXACT",
                "trip_match_status": "EXACT",
            }
            source_hash = hashlib.sha256(example_id.encode()).hexdigest()
            final_row = FinalFeatureRow(
                example_id=example_id,
                source_example_sha256=source_hash,
                anchor_id=str(feature["anchor_observation_id"]),
                service_date=service_date,
                analysis_weight=float(feature["analysis_weight"]),
                query=query,
                feature_values=values,
                slices=tuple(sorted(slices.items(), key=lambda item: item[0].encode())),
            )
            if not math.isfinite(final_row.analysis_weight):
                raise YearDatasetError("final analysis weight must be finite")
            inputs.append(FeatureTransformInput(example_id, value_map))
            rows.append(final_row)
            digest.update(
                _canonical_json(
                    {
                        "analysis_weight": final_row.analysis_weight,
                        "example_id": example_id,
                        "feature_partition_sha256": feature_entries[service_date_text]["sha256"],
                        "selection_partition_sha256": selection_entries[service_date_text][
                            "sha256"
                        ],
                        "service_date": service_date_text,
                        "source_example_sha256": source_hash,
                    }
                )
            )
            digest.update(b"\n")
        matrices.append(context.feature_transform.transform(inputs))
    matrix = sparse.vstack(matrices, format="csr", dtype=np.float32)
    matrix.sort_indices()
    return FinalFeatureInventory(
        context=context,
        features=matrix,
        rows=tuple(rows),
        service_dates=tuple(sorted(feature_entries)),
        row_manifest_sha256=digest.hexdigest(),
    )


def open_final_outcomes(
    inventory: FinalFeatureInventory,
    access: FinalTestAccess,
) -> FinalEvaluationData:
    """Open every sealed final partition exactly through the hash-bound access token."""

    outcome_entries = {
        str(entry["service_date"]): entry["outcomes"]
        for entry in inventory.context.unsampled_manifest["daily_partitions"]
        if entry["split"] == DatasetSplit.FINAL_TEST.value
    }
    if set(outcome_entries) != set(inventory.service_dates):
        raise YearDatasetError("sealed outcome partitions do not align with final features")
    outcomes: dict[str, dict[str, Any]] = {}
    partition_hashes: list[dict[str, str]] = []
    for service_date_text in inventory.service_dates:
        entry = outcome_entries[service_date_text]
        path = _verified_path(
            inventory.context.dataset_root, entry, "sealed final outcome partition"
        )
        table = read_outcome_partition(
            path,
            split=DatasetSplit.FINAL_TEST,
            requesting_milestone=access.requesting_milestone,
        )
        for raw in table.to_pylist():
            example_id = str(raw["example_id"])
            if example_id in outcomes:
                raise YearDatasetError("final outcome example identifiers must be unique")
            outcomes[example_id] = raw
        partition_hashes.append({"service_date": service_date_text, "sha256": str(entry["sha256"])})
    states: list[str] = []
    lower: list[float] = []
    upper: list[float] = []
    row_digest = hashlib.sha256()
    for row in inventory.rows:
        outcome = outcomes.get(row.example_id)
        if outcome is None:
            raise YearDatasetError("a selected final feature row has no outcome record")
        state = str(outcome["outcome_state"])
        lower_value = outcome["lower_bound_seconds"]
        upper_value = outcome["upper_bound_seconds"]
        lower_number = math.nan if lower_value is None else float(lower_value)
        upper_number = math.nan if upper_value is None else float(upper_value)
        if state in LIKELIHOOD_STATES and (math.isnan(lower_number) or math.isnan(upper_number)):
            raise YearDatasetError("likelihood-eligible final outcome is missing bounds")
        if state in FINITE_UPPER_STATES and not math.isfinite(upper_number):
            raise YearDatasetError("finite-upper final outcome has an invalid upper bound")
        states.append(state)
        lower.append(lower_number)
        upper.append(upper_number)
        row_digest.update(
            _canonical_json(
                {
                    "example_id": row.example_id,
                    "lower_bound_seconds": _digest_bound(lower_number),
                    "outcome_state": state,
                    "upper_bound_seconds": _digest_bound(upper_number),
                }
            )
        )
        row_digest.update(b"\n")
    return FinalEvaluationData(
        inventory=inventory,
        outcome_states=tuple(states),
        lower_bounds=np.asarray(lower, dtype=np.float64),
        upper_bounds=np.asarray(upper, dtype=np.float64),
        outcome_manifest_sha256=_sha256(
            {
                "partition_hashes": partition_hashes,
                "row_sha256": row_digest.hexdigest(),
            }
        ),
        access=access,
    )
