from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from arrive90_evaluation.final_artifacts import (
    LoadedModelRegistry,
    _active_station_coordinate,
    _station_coordinate_rows,
    build_replay_selection,
    copy_promoted_bundle,
    evaluation_code_sha256,
    file_sha256,
    load_empirical_baseline,
    load_model_registry,
    load_prediction_artifact,
    value_sha256,
    write_content_addressed_json,
    write_prediction_artifact,
    write_pretty_json,
    write_replay_artifacts,
)
from arrive90_evaluation.final_data import (
    FinalEvaluationData,
    FinalFeatureInventory,
    FinalFeatureRow,
    FinalTestAccess,
)
from arrive90_evaluation.final_metrics import FinalModelPredictions
from arrive90_evaluation.modeling_data import ModelingContext
from arrive90_features.transform import FeatureTransformInput, fit_feature_transform
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_outcomes.travel_time_baselines import (
    EmpiricalMidpointBaseline,
    EmpiricalMidpointQuery,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
        else:
            values[name] = 1.0
    values["scheduled_remaining_seconds"] = 300.0
    values["anchor_latitude"] = 42.1
    values["anchor_longitude"] = -71.1
    return values


def _inventory() -> FinalFeatureInventory:
    values = _values()
    transform = fit_feature_transform((FeatureTransformInput("training", values),))
    context = ModelingContext(
        dataset_root=Path("dataset"),
        population_manifest={},
        population_manifest_sha256=_hash("population"),
        unsampled_manifest={"daily_partitions": []},
        unsampled_manifest_sha256=_hash("unsampled"),
        normalized_manifest_sha256=_hash("normalized"),
        source_lock_sha256=_hash("source"),
        feature_transform=transform,
        feature_transform_sha256=_hash("transform"),
        split_manifest_sha256=_hash("split"),
    )
    rows: list[FinalFeatureRow] = []
    inputs: list[FeatureTransformInput] = []
    for index in range(400):
        service_date = date(2024, 11 if index < 200 else 12, 1)
        example_id = f"example-{index:03d}"
        query = EmpiricalMidpointQuery(
            anchor_id=f"anchor-{index:03d}",
            route_id="Blue",
            direction_id="0",
            origin_stop_id="origin",
            destination_stop_id="destination",
            destination_offset=1,
            day_type="WEEKDAY",
            time_bucket="00:00-03:00",
        )
        rows.append(
            FinalFeatureRow(
                example_id,
                _hash(example_id),
                query.anchor_id,
                service_date,
                1.0,
                query,
                tuple(values.items()),
                (
                    ("day_type", "WEEKDAY"),
                    ("destination_class", "IMMEDIATE"),
                ),
            )
        )
        inputs.append(FeatureTransformInput(example_id, values))
    return FinalFeatureInventory(
        context,
        transform.transform(inputs),
        tuple(rows),
        ("2024-11-01", "2024-12-01"),
        _hash("rows"),
    )


class _DummyPromoted:
    def __init__(self) -> None:
        self.manifest = SimpleNamespace(manifest_hash="a" * 64)

    def raw_margins(self, features: Any) -> np.ndarray:
        return np.zeros(features.shape[0], dtype=np.float64)


def _schedule(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE stops (
              stop_id TEXT, stop_lat REAL, stop_lon REAL,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            INSERT INTO stops VALUES ('origin', 42.0, -71.0, 20240101, 20241231);
            """
        )


def _predictions(row_count: int) -> FinalModelPredictions:
    probabilities = np.tile(np.asarray([0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 0.95]), (row_count, 1))
    quantiles = np.tile(np.asarray([600.0, 900.0, 1200.0]), (row_count, 1))
    return FinalModelPredictions(
        "FULL-normal",
        "b" * 64,
        "normal",
        1.0,
        np.zeros(row_count),
        probabilities,
        quantiles,
        np.ones((row_count, 3), dtype=np.bool_),
    )


def test_replay_selection_and_prediction_artifacts_are_redacted_and_hashed(
    tmp_path: Path,
) -> None:
    inventory = _inventory()
    schedule = tmp_path / "schedule.db"
    _schedule(schedule)
    selection = build_replay_selection(
        inventory,
        cast(Any, _DummyPromoted()),
        schedule_database=schedule,
        replay_config={
            "maximum_examples": 200,
            "maximum_examples_per_month": 100,
            "position_substitution": "PUBLIC_GTFS_ORIGIN_STOP_COORDINATE",
            "selection_hash": "HMAC_SHA256",
            "selection_seed": "seed",
        },
    )
    assert len(selection.indices) == 200
    assert selection.manifest["final_test_outcomes_opened"] is False
    assert selection.manifest["invariant_candidate_count_by_month"] == {
        "2024-11": 200,
        "2024-12": 200,
    }
    data = FinalEvaluationData(
        inventory,
        ("INTERVAL_RESOLVED",) * 400,
        np.full(400, 590.0),
        np.full(400, 610.0),
        _hash("outcomes"),
        FinalTestAccess(_hash("protocol"), selection.manifest_sha256),
    )
    empirical = EmpiricalMidpointBaseline(
        ((("GLOBAL_DESTINATION_OFFSET", "1"), 600.0, 400, 400),),
        minimum_finite_examples=1,
        minimum_distinct_anchors=1,
    )
    prediction = _predictions(400)
    artifact = write_prediction_artifact(
        tmp_path / "runtime",
        data,
        {prediction.bundle_id: prediction},
        empirical,
        protocol_sha256=data.access.protocol_sha256,
        replay_selection_sha256=selection.manifest_sha256,
        model_order=(prediction.bundle_id,),
    )
    manifest, table = load_prediction_artifact(artifact.manifest_path)
    assert table.num_rows == 400
    assert manifest["prediction_sha256"] == artifact.sha256
    assert "anchor_observation_id" not in table.column_names
    replay = write_replay_artifacts(
        tmp_path / "demo",
        selection,
        data,
        prediction,
        forbidden_fields=(
            "anchor_latitude",
            "anchor_longitude",
            "trip_id",
            "vehicle_id",
        ),
    )
    fixture = json.loads((tmp_path / "demo/replay-fixture.json").read_text())
    assert replay["replay_count"] == 200
    assert fixture["feature_payload_excludes_outcomes"] is True
    assert all(
        "anchor_latitude" not in row["feature_payload"]["registered_values"]
        and "anchor_longitude" not in row["feature_payload"]["registered_values"]
        for row in fixture["replays"]
    )


def test_content_addressing_bundle_copy_and_conflicts_fail_closed(tmp_path: Path) -> None:
    path, digest = write_content_addressed_json(tmp_path, "artifact", {"value": 1})
    assert path.name == f"artifact-{digest}.json"
    pretty = tmp_path / "pretty.json"
    assert len(write_pretty_json(pretty, {"value": 1})) == 64
    pretty.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        write_pretty_json(pretty, {"value": 1})

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"bundle":"demo"}', encoding="utf-8")
    manifest_sha256 = file_sha256(manifest)
    source = tmp_path / "models/registry" / manifest_sha256
    source.mkdir(parents=True)
    for name in ("calibration.json", "model-manifest.json", "model.ubj"):
        (source / name).write_text(name, encoding="utf-8")
    (source / "manifest.json").write_bytes(manifest.read_bytes())
    registry = cast(
        LoadedModelRegistry,
        SimpleNamespace(
            root=tmp_path / "models",
            promoted=SimpleNamespace(manifest=SimpleNamespace(manifest_hash=manifest_sha256)),
        ),
    )
    destination, tree_hash, size = copy_promoted_bundle(
        registry, tmp_path / "demo", size_limit_bytes=10_000
    )
    assert destination.name == manifest_sha256
    assert len(tree_hash) == 64
    assert size > 0
    assert value_sha256({"a": 1}) == value_sha256({"a": 1})

    path.write_text("conflict", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        write_content_addressed_json(tmp_path, "artifact", {"value": 1})
    with pytest.raises(ValueError, match="size limit"):
        copy_promoted_bundle(registry, tmp_path / "too-small", size_limit_bytes=1)
    (destination / "model.ubj").write_text("conflict", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        copy_promoted_bundle(registry, tmp_path / "demo", size_limit_bytes=10_000)


def test_registry_baseline_and_source_freezes_verify_every_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_root = tmp_path / "models"
    entries: list[dict[str, str]] = []
    bundles: dict[str, SimpleNamespace] = {}
    for bundle_index in range(7):
        bundle_id = f"bundle-{bundle_index}"
        manifest_sha256 = _hash(bundle_id)
        entries.append(
            {
                "bundle_id": bundle_id,
                "manifest_sha256": manifest_sha256,
                "registry_path": f"registry/{manifest_sha256}",
            }
        )
        bundles[manifest_sha256] = SimpleNamespace(
            manifest=SimpleNamespace(
                bundle_id=bundle_id,
                final_test_outcomes_opened=False,
                manifest_hash=manifest_sha256,
            )
        )

    def load_bundle(directory: Path, *, full_feature_count: int) -> SimpleNamespace:
        assert full_feature_count == 4
        return bundles[directory.name]

    monkeypatch.setattr(AftPredictiveBundle, "load", staticmethod(load_bundle))
    registry_index = {
        "acceptance_version": "travel-time-v1.2",
        "entries": entries,
        "final_test_outcomes_opened": False,
        "promoted_bundle_id": "bundle-2",
        "promoted_manifest_sha256": entries[2]["manifest_sha256"],
    }
    index_path, index_sha256 = write_content_addressed_json(
        model_root, "registry-index", registry_index
    )
    registry = load_model_registry(model_root, full_feature_count=4)
    assert registry.index_path == index_path
    assert registry.index_sha256 == index_sha256
    assert cast(Any, registry.promoted) is bundles[entries[2]["manifest_sha256"]]

    baseline = EmpiricalMidpointBaseline(
        ((("GLOBAL_DESTINATION_OFFSET", "1"), 600.0, 12, 12),),
        minimum_finite_examples=1,
        minimum_distinct_anchors=1,
    )
    baseline_path = model_root / "point-baselines-test.json"
    write_pretty_json(
        baseline_path,
        {
            "empirical_midpoint": {
                "manifest": baseline.manifest,
                "manifest_sha256": baseline.manifest_sha256,
            }
        },
    )
    loaded, baseline_sha256 = load_empirical_baseline(model_root)
    assert loaded.manifest_sha256 == baseline.manifest_sha256
    assert baseline_sha256 == file_sha256(baseline_path)

    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    assert len(evaluation_code_sha256((source,), root=tmp_path)) == 64
    with pytest.raises(ValueError, match="source is missing"):
        evaluation_code_sha256((tmp_path / "missing.py",), root=tmp_path)


def test_replay_and_prediction_tampering_is_rejected(tmp_path: Path) -> None:
    inventory = _inventory()
    schedule = tmp_path / "schedule.db"
    _schedule(schedule)
    with pytest.raises(ValueError, match="limits"):
        build_replay_selection(
            inventory,
            cast(Any, _DummyPromoted()),
            schedule_database=schedule,
            replay_config={
                "maximum_examples": 199,
                "maximum_examples_per_month": 100,
            },
        )
    with pytest.raises(ValueError, match="schedule database"):
        _station_coordinate_rows(tmp_path / "missing.db")
    duplicate_rows = {
        "origin": [
            (20240101, 20241231, 42.0, -71.0),
            (20240101, 20241231, 43.0, -72.0),
        ]
    }
    assert _active_station_coordinate(duplicate_rows, "origin", date(2024, 11, 1)) is None

    data = FinalEvaluationData(
        inventory,
        ("INTERVAL_RESOLVED",) * 400,
        np.full(400, 590.0),
        np.full(400, 610.0),
        _hash("outcomes"),
        FinalTestAccess(_hash("protocol"), _hash("selection")),
    )
    prediction = _predictions(400)
    empirical = EmpiricalMidpointBaseline(
        ((("GLOBAL_DESTINATION_OFFSET", "1"), 600.0, 400, 400),),
        minimum_finite_examples=1,
        minimum_distinct_anchors=1,
    )
    with pytest.raises(ValueError, match="model order"):
        write_prediction_artifact(
            tmp_path / "runtime",
            data,
            {prediction.bundle_id: prediction},
            empirical,
            protocol_sha256=_hash("protocol"),
            replay_selection_sha256=_hash("selection"),
            model_order=("different",),
        )
    invalid_manifest = tmp_path / "invalid-manifest.json"
    invalid_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="failed verification"):
        load_prediction_artifact(invalid_manifest)

    selection = build_replay_selection(
        inventory,
        cast(Any, _DummyPromoted()),
        schedule_database=schedule,
        replay_config={
            "maximum_examples": 200,
            "maximum_examples_per_month": 100,
            "position_substitution": "PUBLIC_GTFS_ORIGIN_STOP_COORDINATE",
            "selection_hash": "HMAC_SHA256",
            "selection_seed": "seed",
        },
    )
    with pytest.raises(ValueError, match="redaction contract"):
        write_replay_artifacts(
            tmp_path / "forbidden",
            selection,
            data,
            prediction,
            forbidden_fields=("replay_id",),
        )
