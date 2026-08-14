from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from arrive90_models.registry import (
    ModelBundleManifest,
    ModelRegistry,
    RegistryExpectations,
    file_sha256,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _expectations() -> RegistryExpectations:
    return RegistryExpectations(
        acceptance_version="travel-time-v1.2",
        source_lock_sha256=_hash("source"),
        normalized_manifest_sha256=_hash("normalized"),
        dataset_manifest_sha256=_hash("dataset"),
        unsampled_manifest_sha256=_hash("unsampled"),
        split_manifest_sha256=_hash("split"),
        feature_registry_sha256=_hash("registry"),
        feature_transform_sha256=_hash("transform"),
        full_feature_order_sha256=_hash("feature-order"),
        dependency_lock_sha256=_hash("dependencies"),
        code_sha256=_hash("code"),
    )


def _manifest(model: Path, model_manifest: Path, calibration: Path) -> ModelBundleManifest:
    expected = _expectations()
    return ModelBundleManifest(
        bundle_id="FULL-normal-scale-1p0",
        acceptance_version=expected.acceptance_version,
        model_schema_version="travel-time-predictive-bundle-v1",
        bundle_kind="FULL",
        aft_distribution="normal",
        aft_scale=1.0,
        model_config_sha256=_hash("config"),
        source_lock_sha256=expected.source_lock_sha256,
        normalized_manifest_sha256=expected.normalized_manifest_sha256,
        dataset_manifest_sha256=expected.dataset_manifest_sha256,
        unsampled_manifest_sha256=expected.unsampled_manifest_sha256,
        split_manifest_sha256=expected.split_manifest_sha256,
        training_row_manifest_sha256=_hash("training"),
        validation_row_manifest_sha256=_hash("validation"),
        calibration_row_manifest_sha256=_hash("calibration-rows"),
        feature_registry_sha256=expected.feature_registry_sha256,
        feature_transform_sha256=expected.feature_transform_sha256,
        full_feature_order_sha256=expected.full_feature_order_sha256,
        model_feature_names=("x",),
        model_feature_indices=(0,),
        model_sha256=file_sha256(model),
        model_wrapper_manifest_sha256=file_sha256(model_manifest),
        calibrator_sha256=file_sha256(calibration),
        dependency_lock_sha256=expected.dependency_lock_sha256,
        code_sha256=expected.code_sha256,
        xgboost_version="3.3.0",
        numpy_version="2.5.2",
        scipy_version="1.18.0",
        random_seed=90,
        xgboost_thread_count=1,
        final_test_outcomes_opened=False,
    )


def test_model_registry_validates_all_lineage_and_immutable_registration(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.ubj"
    model_manifest = tmp_path / "model-manifest.json"
    calibration = tmp_path / "calibration.json"
    model.write_bytes(b"model")
    model_manifest.write_bytes(b"model manifest")
    calibration.write_bytes(b"calibration")
    manifest = _manifest(model, model_manifest, calibration)
    registry = ModelRegistry(tmp_path / "registry", expectations=_expectations())
    registry.validate(
        manifest,
        model_path=model,
        model_manifest_path=model_manifest,
        calibration_path=calibration,
        promoted=True,
    )
    destination = registry.register(
        manifest,
        model,
        model_manifest,
        calibration,
        promoted=True,
    )
    assert destination.name == manifest.manifest_hash
    assert registry.load_manifest(manifest.manifest_hash) == manifest
    with pytest.raises(ValueError, match="immutable"):
        registry.register(manifest, model, model_manifest, calibration)


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("source_lock_sha256", "SOURCE_LOCK_MISMATCH"),
        ("split_manifest_sha256", "SPLIT_MANIFEST_MISMATCH"),
        ("full_feature_order_sha256", "FEATURE_ORDER_MISMATCH"),
        ("dependency_lock_sha256", "DEPENDENCY_LOCK_MISMATCH"),
    ],
)
def test_model_registry_rejects_changed_lineage(tmp_path: Path, field: str, failure: str) -> None:
    model = tmp_path / "model.ubj"
    model_manifest = tmp_path / "model-manifest.json"
    calibration = tmp_path / "calibration.json"
    model.write_bytes(b"model")
    model_manifest.write_bytes(b"model manifest")
    calibration.write_bytes(b"calibration")
    manifest = _manifest(model, model_manifest, calibration)
    registry = ModelRegistry(tmp_path / "registry", expectations=_expectations())
    changed = replace(manifest, **cast(Any, {field: _hash(f"changed-{field}")}))
    with pytest.raises(ValueError, match=failure):
        registry.validate(
            changed,
            model_path=model,
            model_manifest_path=model_manifest,
            calibration_path=calibration,
        )


def test_model_registry_rejects_changed_model_and_calibrator(tmp_path: Path) -> None:
    model = tmp_path / "model.ubj"
    model_manifest = tmp_path / "model-manifest.json"
    calibration = tmp_path / "calibration.json"
    model.write_bytes(b"model")
    model_manifest.write_bytes(b"model manifest")
    calibration.write_bytes(b"calibration")
    manifest = _manifest(model, model_manifest, calibration)
    registry = ModelRegistry(tmp_path / "registry", expectations=_expectations())
    model.write_bytes(b"changed")
    model_manifest.write_bytes(b"changed")
    calibration.write_bytes(b"changed")
    with pytest.raises(
        ValueError,
        match=r"CALIBRATOR_HASH_MISMATCH.*MODEL_HASH_MISMATCH.*MODEL_MANIFEST_HASH_MISMATCH",
    ):
        registry.validate(
            manifest,
            model_path=model,
            model_manifest_path=model_manifest,
            calibration_path=calibration,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"bundle_id": ""}, "identity"),
        ({"model_schema_version": "future"}, "schema version"),
        ({"final_test_outcomes_opened": True}, "cannot open"),
        ({"source_lock_sha256": "bad"}, "SHA-256"),
        (
            {"model_feature_names": ("x", "x"), "model_feature_indices": (0, 1)},
            "unique",
        ),
        (
            {"model_feature_names": ("x", "y"), "model_feature_indices": (1, 0)},
            "increasing",
        ),
        ({"bundle_kind": "INTERCEPT_ONLY"}, "intercept-only"),
        ({"model_feature_names": ("x", "y")}, "must align"),
        ({"xgboost_thread_count": 2}, "one XGBoost thread"),
    ],
)
def test_model_manifest_rejects_invalid_frozen_contracts(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    model = tmp_path / "model.ubj"
    model_manifest = tmp_path / "model-manifest.json"
    calibration = tmp_path / "calibration.json"
    model.write_bytes(b"model")
    model_manifest.write_bytes(b"model manifest")
    calibration.write_bytes(b"calibration")
    manifest = _manifest(model, model_manifest, calibration)
    with pytest.raises(ValueError, match=message):
        replace(manifest, **cast(Any, changes))


def test_registry_rejects_oversize_bundle_and_invalid_load_hash(tmp_path: Path) -> None:
    model = tmp_path / "model.ubj"
    model_manifest = tmp_path / "model-manifest.json"
    calibration = tmp_path / "calibration.json"
    model.write_bytes(b"model")
    model_manifest.write_bytes(b"model manifest")
    calibration.write_bytes(b"calibration")
    manifest = _manifest(model, model_manifest, calibration)
    registry = ModelRegistry(
        tmp_path / "registry",
        expectations=_expectations(),
        promoted_bundle_size_bytes_max=1,
    )
    with pytest.raises(ValueError, match="PROMOTED_BUNDLE_SIZE_EXCEEDED"):
        registry.validate(
            manifest,
            model_path=model,
            model_manifest_path=model_manifest,
            calibration_path=calibration,
            promoted=True,
        )
    with pytest.raises(ValueError, match="manifest hash"):
        registry.load_manifest("bad")
