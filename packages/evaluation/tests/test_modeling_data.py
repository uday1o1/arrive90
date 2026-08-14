from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_evaluation.model_population import FEATURE_SCHEMA, SELECTED_SCHEMA
from arrive90_evaluation.modeling_data import (
    ModelingContext,
    load_modeling_context,
    load_modeling_split,
)
from arrive90_evaluation.year_dataset import OUTCOME_SCHEMA, FinalTestOutcomeAccessError
from arrive90_features.transform import FeatureTransformInput, fit_feature_transform
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_ingestion.acquisition import sha256_file


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _feature_values() -> dict[str, str | int | float | bool | None]:
    categories = {
        "route_id": "Blue",
        "direction_id": "0",
        "origin_stop_id": "origin",
        "destination_stop_id": "destination",
        "route_pattern_id": "pattern",
    }
    values: dict[str, str | int | float | bool | None] = {}
    for name, spec in TRAVEL_TIME_V1_REGISTRY.specs.items():
        if spec.value_type == "categorical":
            values[name] = categories[name]
        elif spec.value_type == "boolean":
            values[name] = False
        elif spec.value_type == "integer":
            values[name] = 1
        elif spec.value_type == "float_or_null":
            values[name] = None
        else:
            values[name] = 1.0
    values["scheduled_remaining_seconds"] = 300.0
    return values


def _write_parquet(
    path: Path, schema: pa.Schema, rows: list[dict[str, object]]
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return {"path": path.as_posix(), "sha256": sha256_file(path)}


def test_modeling_split_loads_verified_pretest_rows_and_rejects_final(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "dataset"
    values = _feature_values()
    transform = fit_feature_transform((FeatureTransformInput("example", values),))
    feature = cast(
        dict[str, object],
        {
            "example_id": "example",
            "episode_id": "episode",
            "anchor_observation_id": "anchor",
            "service_date": date(2024, 1, 1),
            "split": DatasetSplit.TRAINING.value,
            "base_weight": 1.0,
            "inclusion_probability": 1.0,
            "analysis_weight": 1.0,
            **values,
        },
    )
    selection = {
        "example_id": "example",
        "episode_id": "episode",
        "anchor_observation_id": "anchor",
        "service_date": date(2024, 1, 1),
        "split": DatasetSplit.TRAINING.value,
        "route_id": "Blue",
        "direction_id": 0,
        "feature_cutoff_utc": datetime(2024, 1, 1, 12, tzinfo=UTC),
        "peak_period": "OFF_PEAK",
        "destination_stop_id": "destination",
        "destination_stop_sequence": 2,
        "destination_offset": 1,
        "destination_class": "IMMEDIATE",
        "scheduled_remaining_seconds": 300,
        "base_weight": 1.0,
        "schedule_version_id": "schedule",
        "route_pattern_id": "pattern",
        "selection_digest": _hash("selection"),
        "inclusion_probability": 1.0,
        "analysis_weight": 1.0,
    }
    outcome = {
        "example_id": "example",
        "outcome_state": "LEFT_CENSORED",
        "lower_evidence_observation_id": "anchor",
        "upper_evidence_observation_id": "upper",
        "lower_bound_seconds": 0.0,
        "upper_bound_seconds": 120.0,
    }
    feature_entry = _write_parquet(dataset_root / "features.parquet", FEATURE_SCHEMA, [feature])
    selection_entry = _write_parquet(
        dataset_root / "selection.parquet", SELECTED_SCHEMA, [selection]
    )
    outcome_entry = _write_parquet(dataset_root / "outcomes.parquet", OUTCOME_SCHEMA, [outcome])
    for entry in (feature_entry, selection_entry, outcome_entry):
        entry["path"] = Path(str(entry["path"])).relative_to(dataset_root).as_posix()
    context = ModelingContext(
        dataset_root=dataset_root,
        population_manifest={
            "feature_partitions": [
                {**feature_entry, "service_date": "2024-01-01", "split": "TRAINING"}
            ],
            "selection_partitions": [
                {**selection_entry, "service_date": "2024-01-01", "split": "TRAINING"}
            ],
        },
        population_manifest_sha256=_hash("population"),
        unsampled_manifest={
            "daily_partitions": [
                {
                    "service_date": "2024-01-01",
                    "split": "TRAINING",
                    "outcomes": outcome_entry,
                }
            ]
        },
        unsampled_manifest_sha256=_hash("unsampled"),
        normalized_manifest_sha256=_hash("normalized"),
        source_lock_sha256=_hash("source"),
        feature_transform=transform,
        feature_transform_sha256=_hash("transform"),
        split_manifest_sha256=_hash("split"),
    )
    loaded = load_modeling_split(context, DatasetSplit.TRAINING)
    assert loaded.features.shape == (1, len(transform.column_names))
    assert loaded.lower_bounds.tolist() == [0]
    assert loaded.metadata[0].query.time_bucket == "06:00-09:00"
    with pytest.raises(FinalTestOutcomeAccessError, match="cannot open"):
        load_modeling_split(context, DatasetSplit.FINAL_TEST)
    (dataset_root / "outcomes.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="content verification"):
        load_modeling_split(context, DatasetSplit.TRAINING)


def test_modeling_context_verifies_active_manifests_and_transform(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    normalized_root = tmp_path / "normalized"
    transform = fit_feature_transform((FeatureTransformInput("example", _feature_values()),))
    transform_body = _canonical(
        {
            **transform.manifest,
            "acceptance_version": "travel-time-v1.2",
        }
    )
    transform_sha = hashlib.sha256(transform_body).hexdigest()
    transform_path = dataset_root / "transforms" / f"transform-{transform_sha}.json"
    transform_path.parent.mkdir(parents=True)
    transform_path.write_bytes(transform_body)
    unsampled = {
        "acceptance_version": "travel-time-v1.2",
        "daily_partitions": [
            {
                "service_date": "2024-01-01",
                "split": "TRAINING",
                "outcomes": {"path": "outcomes", "sha256": _hash("outcomes")},
            }
        ],
    }
    unsampled_body = _canonical(unsampled)
    unsampled_sha = hashlib.sha256(unsampled_body).hexdigest()
    unsampled_path = dataset_root / "manifests" / f"unsampled-audit-manifest-{unsampled_sha}.json"
    unsampled_path.parent.mkdir(parents=True)
    unsampled_path.write_bytes(unsampled_body)
    normalized = {
        "acceptance_version": "travel-time-v1.2",
        "acquisition_lock_sha256": _hash("source"),
    }
    normalized_body = _canonical(normalized)
    normalized_sha = hashlib.sha256(normalized_body).hexdigest()
    normalized_path = normalized_root / "manifests/2024" / f"dataset-manifest-{normalized_sha}.json"
    normalized_path.parent.mkdir(parents=True)
    normalized_path.write_bytes(normalized_body)
    population = {
        "acceptance_version": "travel-time-v1.2",
        "feature_partitions": [
            {"service_date": "2024-01-01", "sha256": _hash("features"), "split": "TRAINING"}
        ],
        "normalized_manifest_sha256": normalized_sha,
        "selection_partitions": [
            {"service_date": "2024-01-01", "sha256": _hash("selection"), "split": "TRAINING"}
        ],
        "transform": {
            "path": transform_path.relative_to(dataset_root).as_posix(),
            "sha256": transform_sha,
        },
        "unsampled_manifest_sha256": unsampled_sha,
    }
    population_body = _canonical(population)
    population_sha = hashlib.sha256(population_body).hexdigest()
    population_path = (
        dataset_root / "manifests" / f"model-population-manifest-{population_sha}.json"
    )
    population_path.write_bytes(population_body)
    (dataset_root / "manifests/active-unsampled.json").write_bytes(
        _canonical(
            {
                "acceptance_version": "travel-time-v1.2",
                "path": unsampled_path.relative_to(dataset_root).as_posix(),
                "sha256": unsampled_sha,
            }
        )
    )
    (dataset_root / "manifests/active-model-population.json").write_bytes(
        _canonical(
            {
                "acceptance_version": "travel-time-v1.2",
                "path": population_path.relative_to(dataset_root).as_posix(),
                "sha256": population_sha,
            }
        )
    )
    context = load_modeling_context(dataset_root, normalized_root=normalized_root)
    assert context.population_manifest_sha256 == population_sha
    assert context.unsampled_manifest_sha256 == unsampled_sha
    assert context.source_lock_sha256 == _hash("source")
    assert len(context.split_manifest_sha256) == 64
