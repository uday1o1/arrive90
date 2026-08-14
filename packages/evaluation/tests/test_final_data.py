from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_evaluation.final_data import (
    FinalEvaluationData,
    FinalFeatureInventory,
    FinalTestAccess,
    _deviation_bucket,
    _digest_bound,
    _gap_bucket,
    _scheduled_bucket,
    _season,
    load_final_feature_inventory,
    open_final_outcomes,
)
from arrive90_evaluation.model_population import FEATURE_SCHEMA, SELECTED_SCHEMA
from arrive90_evaluation.modeling_data import ModelingContext
from arrive90_evaluation.year_dataset import OUTCOME_SCHEMA
from arrive90_features.transform import FeatureTransformInput, fit_feature_transform
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_ingestion.acquisition import sha256_file


def _values() -> dict[str, str | int | float | bool | None]:
    values: dict[str, str | int | float | bool | None] = {}
    for name, spec in TRAVEL_TIME_V1_REGISTRY.specs.items():
        if spec.value_type == "categorical":
            values[name] = {
                "route_id": "Blue",
                "direction_id": "0",
                "origin_stop_id": "origin",
                "destination_stop_id": "destination",
                "route_pattern_id": "pattern",
            }[name]
        elif spec.value_type == "boolean":
            values[name] = False
        elif spec.value_type == "integer":
            values[name] = 1
        elif spec.value_type == "float_or_null":
            values[name] = 1.0
        else:
            values[name] = 1.0
    values["scheduled_remaining_seconds"] = 300.0
    values["observed_origin_lateness_seconds"] = -30.0
    values["most_recent_observation_gap_seconds"] = None
    values["anchor_latitude"] = 42.0
    values["anchor_longitude"] = -71.0
    return values


def _write(path: Path, row: dict[str, object], schema: pa.Schema) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([row], schema=schema), path)
    return {
        "path": path.as_posix(),
        "row_count": 1,
        "sha256": sha256_file(path),
    }


def _context(tmp_path: Path) -> ModelingContext:
    dataset_root = tmp_path / "dataset"
    values = _values()
    transform = fit_feature_transform((FeatureTransformInput("training", values),))
    feature_entries: list[dict[str, object]] = []
    selection_entries: list[dict[str, object]] = []
    outcome_days: list[dict[str, object]] = []
    current = date(2024, 11, 1)
    while current <= date(2024, 12, 31):
        example_id = f"example-{current.isoformat()}"
        feature_row: dict[str, object] = {
            "example_id": example_id,
            "episode_id": f"episode-{current.isoformat()}",
            "anchor_observation_id": f"anchor-{current.isoformat()}",
            "service_date": current,
            "split": DatasetSplit.FINAL_TEST.value,
            "base_weight": 1.0,
            "inclusion_probability": 1.0,
            "analysis_weight": 1.0,
            **values,
        }
        selection_row: dict[str, object] = {
            "example_id": example_id,
            "episode_id": feature_row["episode_id"],
            "anchor_observation_id": feature_row["anchor_observation_id"],
            "service_date": current,
            "split": DatasetSplit.FINAL_TEST.value,
            "route_id": "Blue",
            "direction_id": 0,
            "feature_cutoff_utc": datetime.combine(current, datetime.min.time(), tzinfo=UTC),
            "peak_period": "OFF_PEAK",
            "destination_stop_id": "destination",
            "destination_stop_sequence": 2,
            "destination_offset": 1,
            "destination_class": "IMMEDIATE",
            "scheduled_remaining_seconds": 300,
            "base_weight": 1.0,
            "schedule_version_id": "schedule",
            "route_pattern_id": "pattern",
            "selection_digest": hashlib.sha256(example_id.encode()).hexdigest(),
            "inclusion_probability": 1.0,
            "analysis_weight": 1.0,
        }
        outcome_row = {
            "example_id": example_id,
            "outcome_state": "INTERVAL_RESOLVED",
            "lower_evidence_observation_id": "lower",
            "upper_evidence_observation_id": "upper",
            "lower_bound_seconds": 290.0,
            "upper_bound_seconds": 310.0,
        }
        feature = _write(
            dataset_root / f"features/{current.isoformat()}.parquet",
            feature_row,
            FEATURE_SCHEMA,
        )
        feature.update(
            {"service_date": current.isoformat(), "split": DatasetSplit.FINAL_TEST.value}
        )
        selection = _write(
            dataset_root / f"selection/{current.isoformat()}.parquet",
            selection_row,
            SELECTED_SCHEMA,
        )
        selection.update(
            {"service_date": current.isoformat(), "split": DatasetSplit.FINAL_TEST.value}
        )
        outcome = _write(
            dataset_root / f"outcomes/{current.isoformat()}.parquet",
            outcome_row,
            OUTCOME_SCHEMA,
        )
        outcome["sealed"] = True
        feature_entries.append(feature)
        selection_entries.append(selection)
        outcome_days.append(
            {
                "audit_projection": {"outcome_state_counts": {"INTERVAL_RESOLVED": 1}},
                "outcomes": outcome,
                "service_date": current.isoformat(),
                "split": DatasetSplit.FINAL_TEST.value,
            }
        )
        current += timedelta(days=1)
    return ModelingContext(
        dataset_root=dataset_root,
        population_manifest={
            "feature_partitions": feature_entries,
            "selection_partitions": selection_entries,
        },
        population_manifest_sha256="a" * 64,
        unsampled_manifest={"daily_partitions": outcome_days},
        unsampled_manifest_sha256="b" * 64,
        normalized_manifest_sha256="c" * 64,
        source_lock_sha256="d" * 64,
        feature_transform=transform,
        feature_transform_sha256="e" * 64,
        split_manifest_sha256="f" * 64,
    )


def test_final_inventory_is_outcome_free_until_hash_bound_access(tmp_path: Path) -> None:
    context = _context(tmp_path)
    inventory = load_final_feature_inventory(context)
    assert len(inventory.rows) == 61
    assert inventory.features.shape[0] == 61
    assert inventory.service_dates[0] == "2024-11-01"
    assert inventory.service_dates[-1] == "2024-12-31"
    assert not inventory.final_test_outcomes_opened
    assert inventory.rows[0].slice_values["observation_gap_bucket"] == "MISSING"
    assert inventory.rows[0].slice_values["anchor_schedule_deviation_bucket"] == "LOW"

    access = FinalTestAccess("1" * 64, "2" * 64)
    data = open_final_outcomes(inventory, access)
    assert data.likelihood_mask.all()
    assert data.finite_upper_mask.all()
    assert data.lower_bounds.tolist() == [290.0] * 61
    assert data.analysis_weights.tolist() == [1.0] * 61
    assert len(data.outcome_manifest_sha256) == 64


def test_final_access_and_inventory_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        FinalTestAccess("bad", "2" * 64)
    with pytest.raises(ValueError, match="Milestone 4"):
        FinalTestAccess("1" * 64, "2" * 64, requesting_milestone=3)
    context = _context(tmp_path)
    context.population_manifest["feature_partitions"].pop()
    with pytest.raises(ValueError, match="do not align"):
        load_final_feature_inventory(context)


def test_final_slice_buckets_and_data_contract_cover_all_boundaries(tmp_path: Path) -> None:
    assert _digest_bound(math.nan) is None
    assert _digest_bound(math.inf) == "POSITIVE_INFINITY"
    assert _digest_bound(-math.inf) == "NEGATIVE_INFINITY"
    assert _digest_bound(12.5) == 12.5
    assert [_scheduled_bucket(value) for value in (1, 600, 601, 1200, 1201, 1800)] == [
        "SHORT",
        "SHORT",
        "MEDIUM",
        "MEDIUM",
        "LONG",
        "LONG",
    ]
    with pytest.raises(ValueError, match="outside"):
        _scheduled_bucket(1801)
    assert [_deviation_bucket(value) for value in (0, 60, 61, 300, 301)] == [
        "LOW",
        "LOW",
        "TYPICAL",
        "TYPICAL",
        "HIGH",
    ]
    assert [_gap_bucket(value) for value in (None, 0, 75, 76, 180, 181, 600)] == [
        "MISSING",
        "LOW",
        "LOW",
        "TYPICAL",
        "TYPICAL",
        "HIGH",
        "HIGH",
    ]
    with pytest.raises(ValueError, match="outside"):
        _gap_bucket(601)
    assert [_season(month) for month in (1, 4, 7, 10, 12)] == [
        "WINTER",
        "SPRING",
        "SUMMER",
        "FALL",
        "WINTER",
    ]

    inventory = load_final_feature_inventory(_context(tmp_path))
    row = inventory.rows[0]
    with pytest.raises(ValueError, match="identity"):
        replace(row, example_id="")
    with pytest.raises(ValueError, match="SHA-256"):
        replace(row, source_example_sha256="bad")
    with pytest.raises(ValueError, match="canonical order"):
        replace(row, slices=tuple(reversed(row.slices)))
    with pytest.raises(ValueError, match="cannot contain"):
        replace(inventory, final_test_outcomes_opened=True)
    with pytest.raises(ValueError, match="does not align"):
        replace(inventory, features=inventory.features[:-1])
    with pytest.raises(ValueError, match="unique"):
        FinalFeatureInventory(
            inventory.context,
            inventory.features[:2],
            (row, row),
            inventory.service_dates,
            inventory.row_manifest_sha256,
        )

    access = FinalTestAccess("1" * 64, "2" * 64)
    data = open_final_outcomes(inventory, access)
    with pytest.raises(ValueError, match="do not align"):
        replace(data, outcome_states=data.outcome_states[:-1])
    with pytest.raises(ValueError, match="unknown state"):
        FinalEvaluationData(
            inventory,
            ("UNKNOWN",) * len(inventory.rows),
            data.lower_bounds,
            data.upper_bounds,
            data.outcome_manifest_sha256,
            access,
        )

    inventory.context.unsampled_manifest["daily_partitions"].pop()
    with pytest.raises(ValueError, match="do not align"):
        open_final_outcomes(inventory, access)
