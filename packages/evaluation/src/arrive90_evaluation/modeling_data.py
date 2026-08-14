"""Verified pretest data loading for travel-time model training and calibration."""

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
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.travel_time import DownstreamOutcomeState
from arrive90_features.transform import FeatureTransformInput, FeatureValue, FittedFeatureTransform
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_ingestion.acquisition import sha256_file
from arrive90_outcomes.travel_time_baselines import EmpiricalMidpointQuery, three_hour_bucket
from scipy import sparse  # type: ignore[import-untyped]

from arrive90_evaluation.model_population import FEATURE_SCHEMA, SELECTED_SCHEMA
from arrive90_evaluation.year_dataset import (
    DEFAULT_DATASET_ROOT,
    FinalTestOutcomeAccessError,
    YearDatasetError,
    read_outcome_partition,
)

BOSTON = ZoneInfo("America/New_York")
ELIGIBLE_STATES = frozenset(
    {
        DownstreamOutcomeState.INTERVAL_RESOLVED.value,
        DownstreamOutcomeState.LEFT_CENSORED.value,
        DownstreamOutcomeState.RIGHT_CENSORED.value,
    }
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise YearDatasetError(f"{path} must contain an object")
    return value


def _verify(root: Path, entry: dict[str, Any], label: str) -> Path:
    path = root / str(entry.get("path", ""))
    if not path.is_file() or sha256_file(path) != entry.get("sha256"):
        raise YearDatasetError(f"{label} failed content verification: {path}")
    return path


def _active_manifest(
    root: Path, pointer_name: str, expected_prefix: str
) -> tuple[Path, dict[str, Any], str]:
    pointer_path = root / "manifests" / pointer_name
    pointer = _load_json(pointer_path)
    if pointer.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
        raise YearDatasetError("active model-data pointer has the wrong acceptance version")
    path = root / str(pointer.get("path", ""))
    digest = str(pointer.get("sha256", ""))
    if (
        not path.is_file()
        or sha256_file(path) != digest
        or not path.name.startswith(expected_prefix)
    ):
        raise YearDatasetError("active model-data manifest failed content verification")
    return path, _load_json(path), digest


def _transform_from_manifest(payload: dict[str, Any]) -> FittedFeatureTransform:
    vocabularies = payload.get("categorical_vocabularies")
    column_names = payload.get("column_names")
    if not isinstance(vocabularies, dict) or not isinstance(column_names, list):
        raise YearDatasetError("feature transform manifest is incomplete")
    return FittedFeatureTransform(
        training_row_sha256=str(payload["training_row_sha256"]),
        vocabularies=tuple(
            (name, tuple(str(value) for value in vocabularies[name]))
            for name in (
                "route_id",
                "direction_id",
                "origin_stop_id",
                "destination_stop_id",
                "route_pattern_id",
            )
        ),
        column_names=tuple(str(value) for value in column_names),
        output_schema_sha256=str(payload["output_schema_sha256"]),
        csr_index_dtype=str(payload["csr_index_dtype"]),
        value_dtype=str(payload["value_dtype"]),
        version=str(payload["version"]),
    )


@dataclass(frozen=True, slots=True)
class ModelingContext:
    dataset_root: Path
    population_manifest: dict[str, Any]
    population_manifest_sha256: str
    unsampled_manifest: dict[str, Any]
    unsampled_manifest_sha256: str
    normalized_manifest_sha256: str
    source_lock_sha256: str
    feature_transform: FittedFeatureTransform
    feature_transform_sha256: str
    split_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ExampleMetadata:
    example_id: str
    anchor_id: str
    service_date: date
    query: EmpiricalMidpointQuery
    scheduled_remaining_seconds: float


@dataclass(frozen=True, slots=True)
class ModelingSplit:
    split: DatasetSplit
    features: sparse.csr_matrix
    lower_bounds: np.ndarray
    upper_bounds: np.ndarray
    analysis_weights: np.ndarray
    example_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    outcome_states: tuple[str, ...]
    metadata: tuple[ExampleMetadata, ...]
    row_manifest_sha256: str
    service_dates: tuple[str, ...]

    def __post_init__(self) -> None:
        row_count = self.features.shape[0]
        if row_count == 0 or any(
            len(values) != row_count
            for values in (
                self.lower_bounds,
                self.upper_bounds,
                self.analysis_weights,
                self.example_ids,
                self.anchor_ids,
                self.outcome_states,
                self.metadata,
            )
        ):
            raise ValueError("modeling split arrays must align and be nonempty")


def load_modeling_context(
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    *,
    normalized_root: Path = Path("data/normalized"),
) -> ModelingContext:
    _, population, population_sha256 = _active_manifest(
        dataset_root, "active-model-population.json", "model-population-manifest-"
    )
    if population.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
        raise YearDatasetError("model population uses the wrong acceptance version")
    _, unsampled, unsampled_sha256 = _active_manifest(
        dataset_root, "active-unsampled.json", "unsampled-audit-manifest-"
    )
    if population.get("unsampled_manifest_sha256") != unsampled_sha256:
        raise YearDatasetError("model population and unsampled manifests do not match")
    normalized_sha256 = str(population.get("normalized_manifest_sha256", ""))
    normalized_path = (
        normalized_root / "manifests/2024" / f"dataset-manifest-{normalized_sha256}.json"
    )
    if not normalized_path.is_file() or sha256_file(normalized_path) != normalized_sha256:
        raise YearDatasetError("normalized model input manifest failed verification")
    normalized = _load_json(normalized_path)
    source_lock_sha256 = str(normalized.get("acquisition_lock_sha256", ""))
    if len(source_lock_sha256) != 64:
        raise YearDatasetError("normalized manifest source-lock lineage is invalid")
    transform_entry = population.get("transform")
    if not isinstance(transform_entry, dict):
        raise YearDatasetError("model population transform entry is invalid")
    transform_path = _verify(dataset_root, transform_entry, "feature transform")
    transform_sha256 = str(transform_entry["sha256"])
    transform = _transform_from_manifest(_load_json(transform_path))
    split_payload = {
        "feature_partitions": [
            {
                "service_date": entry["service_date"],
                "sha256": entry["sha256"],
                "split": entry["split"],
            }
            for entry in population["feature_partitions"]
        ],
        "outcome_partitions": [
            {
                "service_date": entry["service_date"],
                "sha256": entry["outcomes"]["sha256"],
                "split": entry["split"],
            }
            for entry in unsampled["daily_partitions"]
        ],
        "selection_partitions": [
            {
                "service_date": entry["service_date"],
                "sha256": entry["sha256"],
                "split": entry["split"],
            }
            for entry in population["selection_partitions"]
        ],
    }
    return ModelingContext(
        dataset_root=dataset_root,
        population_manifest=population,
        population_manifest_sha256=population_sha256,
        unsampled_manifest=unsampled,
        unsampled_manifest_sha256=unsampled_sha256,
        normalized_manifest_sha256=normalized_sha256,
        source_lock_sha256=source_lock_sha256,
        feature_transform=transform,
        feature_transform_sha256=transform_sha256,
        split_manifest_sha256=hashlib.sha256(_canonical_json(split_payload)).hexdigest(),
    )


def _feature_input(raw: dict[str, Any]) -> FeatureTransformInput:
    return FeatureTransformInput(
        row_id=str(raw["example_id"]),
        values={name: cast(FeatureValue, raw[name]) for name in TRAVEL_TIME_V1_REGISTRY.specs},
    )


def _outcome_bound(value: object, *, upper: bool) -> float:
    if value is None:
        raise YearDatasetError("likelihood-eligible outcomes require both AFT bounds")
    result = float(cast(Any, value))
    if math.isnan(result) or (not upper and not math.isfinite(result)):
        raise YearDatasetError("likelihood-eligible outcome bounds are invalid")
    return result


def load_modeling_split(context: ModelingContext, split: DatasetSplit) -> ModelingSplit:
    """Load one verified pretest split and reject final-test access."""

    if split is DatasetSplit.FINAL_TEST:
        raise FinalTestOutcomeAccessError("Milestone 3 cannot open final-test duration bounds")
    feature_entries = {
        str(entry["service_date"]): entry
        for entry in context.population_manifest["feature_partitions"]
        if entry["split"] == split.value
    }
    selection_entries = {
        str(entry["service_date"]): entry
        for entry in context.population_manifest["selection_partitions"]
        if entry["split"] == split.value
    }
    outcome_entries = {
        str(entry["service_date"]): entry["outcomes"]
        for entry in context.unsampled_manifest["daily_partitions"]
        if entry["split"] == split.value
    }
    if (
        not feature_entries
        or set(feature_entries) != set(selection_entries)
        or set(feature_entries) != set(outcome_entries)
    ):
        raise YearDatasetError("modeling split partition inventories do not align")
    matrices: list[sparse.csr_matrix] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    weights: list[float] = []
    example_ids: list[str] = []
    anchor_ids: list[str] = []
    states: list[str] = []
    metadata: list[ExampleMetadata] = []
    row_digest = hashlib.sha256()
    for service_date_text in sorted(feature_entries):
        feature_path = _verify(
            context.dataset_root, feature_entries[service_date_text], "model feature partition"
        )
        selection_path = _verify(
            context.dataset_root,
            selection_entries[service_date_text],
            "model selection partition",
        )
        outcome_path = _verify(
            context.dataset_root, outcome_entries[service_date_text], "model outcome partition"
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
            raise YearDatasetError("selected features and candidate metadata are not aligned")
        outcomes = {
            str(row["example_id"]): row
            for row in read_outcome_partition(
                outcome_path, split=split, requesting_milestone=3
            ).to_pylist()
            if row["outcome_state"] in ELIGIBLE_STATES
        }
        inputs: list[FeatureTransformInput] = []
        for feature in feature_rows:
            example_id = str(feature["example_id"])
            selection = selection_by_id[example_id]
            outcome = outcomes.get(example_id)
            if outcome is None:
                continue
            lower = _outcome_bound(outcome["lower_bound_seconds"], upper=False)
            upper = _outcome_bound(outcome["upper_bound_seconds"], upper=True)
            weight = float(feature["analysis_weight"])
            anchor_id = str(feature["anchor_observation_id"])
            cutoff = selection["feature_cutoff_utc"]
            if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
                raise YearDatasetError("selected feature cutoff must be timezone aware")
            service_date = date.fromisoformat(service_date_text)
            local_hour = cutoff.astimezone(BOSTON).hour
            query = EmpiricalMidpointQuery(
                anchor_id=anchor_id,
                route_id=str(selection["route_id"]),
                direction_id=str(selection["direction_id"]),
                origin_stop_id=str(feature["origin_stop_id"]),
                destination_stop_id=str(selection["destination_stop_id"]),
                destination_offset=int(selection["destination_offset"]),
                day_type="WEEKDAY" if service_date.isoweekday() <= 5 else "WEEKEND",
                time_bucket=three_hour_bucket(local_hour),
            )
            if lower < 0 or upper <= 0 or upper < lower or weight <= 0:
                raise YearDatasetError("model split labels or weights violate the AFT contract")
            inputs.append(_feature_input(feature))
            lower_bounds.append(lower)
            upper_bounds.append(upper)
            weights.append(weight)
            example_ids.append(example_id)
            anchor_ids.append(anchor_id)
            states.append(str(outcome["outcome_state"]))
            metadata.append(
                ExampleMetadata(
                    example_id,
                    anchor_id,
                    service_date,
                    query,
                    float(selection["scheduled_remaining_seconds"]),
                )
            )
            row_digest.update(
                _canonical_json(
                    {
                        "analysis_weight": weight,
                        "anchor_id": anchor_id,
                        "example_id": example_id,
                        "lower_bound_seconds": lower,
                        "outcome_state": outcome["outcome_state"],
                        "service_date": service_date_text,
                        "upper_bound_seconds": "Infinity" if math.isinf(upper) else upper,
                    }
                )
            )
            row_digest.update(b"\n")
        if inputs:
            matrices.append(context.feature_transform.transform(inputs))
    if not matrices:
        raise YearDatasetError(f"{split.value} has no likelihood-eligible model examples")
    matrix = sparse.vstack(matrices, format="csr", dtype=np.float32)
    matrix.sort_indices()
    return ModelingSplit(
        split=split,
        features=matrix,
        lower_bounds=np.asarray(lower_bounds, dtype=np.float64),
        upper_bounds=np.asarray(upper_bounds, dtype=np.float64),
        analysis_weights=np.asarray(weights, dtype=np.float64),
        example_ids=tuple(example_ids),
        anchor_ids=tuple(anchor_ids),
        outcome_states=tuple(states),
        metadata=tuple(metadata),
        row_manifest_sha256=row_digest.hexdigest(),
        service_dates=tuple(sorted(feature_entries)),
    )
