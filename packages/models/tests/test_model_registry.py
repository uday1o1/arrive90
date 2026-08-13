from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from arrive90_models.registry import ModelBundleManifest, ModelRegistry, file_sha256


def _manifest(model: Path, calibration: Path) -> ModelBundleManifest:
    return ModelBundleManifest(
        "bundle-v1",
        "historical_v1",
        "historical_v1",
        "features",
        "STATIC_ROUTE_POLICY_V1",
        "candidates",
        "decision-v1",
        "alerts",
        "mask",
        "training",
        "calibration-rows",
        file_sha256(model),
        file_sha256(calibration),
        "v1",
        "3.3.0",
    )


def test_model_registry_validates_hashes_lineage_and_immutable_registration(tmp_path: Path) -> None:
    model = tmp_path / "model.ubj"
    calibration = tmp_path / "calibration.json"
    model.write_bytes(b"model")
    calibration.write_bytes(b"calibration")
    manifest = _manifest(model, calibration)
    registry = ModelRegistry(tmp_path / "registry", expected_feature_schema="historical_v1")
    registry.validate(
        manifest,
        model_path=model,
        calibration_path=calibration,
        evaluation_candidate_manifest_hash="candidates",
        evaluation_decision_context_version="decision-v1",
        evaluation_alert_lineage_hash="alerts",
        evaluation_eligibility_mask_hash="mask",
    )
    destination = registry.register(manifest, model, calibration)
    assert (destination / "manifest.json").is_file()
    assert len(manifest.manifest_hash) == 64
    with pytest.raises(ValueError, match="immutable"):
        registry.register(manifest, model, calibration)
    with pytest.raises(ValueError, match="FEATURE_SCHEMA_MISMATCH"):
        registry.validate(
            replace(manifest, feature_schema_version="future"),
            model_path=model,
            calibration_path=calibration,
            evaluation_candidate_manifest_hash="candidates",
            evaluation_decision_context_version="decision-v1",
            evaluation_alert_lineage_hash="alerts",
            evaluation_eligibility_mask_hash="mask",
        )
