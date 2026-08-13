"""Immutable model-bundle registry with schema and lineage validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ModelBundleManifest:
    bundle_id: str
    model_schema: str
    feature_schema_version: str
    feature_registry_hash: str
    candidate_generator_mode: str
    candidate_manifest_hash: str
    decision_context_version: str
    alert_lineage_hash: str
    eligibility_mask_hash: str
    training_row_manifest_hash: str
    calibration_row_manifest_hash: str
    model_sha256: str
    calibration_sha256: str
    api_compatibility: str
    xgboost_version: str

    @property
    def manifest_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()


class ModelRegistry:
    def __init__(
        self,
        root: Path,
        *,
        expected_feature_schema: str,
        expected_candidate_generator_mode: str = "STATIC_ROUTE_POLICY_V1",
        expected_api_compatibility: str = "v1",
    ) -> None:
        self.root = root
        self.expected_feature_schema = expected_feature_schema
        self.expected_candidate_generator_mode = expected_candidate_generator_mode
        self.expected_api_compatibility = expected_api_compatibility

    def validate(
        self,
        manifest: ModelBundleManifest,
        *,
        model_path: Path,
        calibration_path: Path,
        evaluation_candidate_manifest_hash: str,
        evaluation_decision_context_version: str,
        evaluation_alert_lineage_hash: str,
        evaluation_eligibility_mask_hash: str,
    ) -> None:
        failures: list[str] = []
        if manifest.feature_schema_version != self.expected_feature_schema:
            failures.append("FEATURE_SCHEMA_MISMATCH")
        if manifest.candidate_generator_mode != self.expected_candidate_generator_mode:
            failures.append("CANDIDATE_GENERATOR_MODE_MISMATCH")
        if manifest.api_compatibility != self.expected_api_compatibility:
            failures.append("API_COMPATIBILITY_MISMATCH")
        if manifest.model_sha256 != file_sha256(model_path):
            failures.append("MODEL_HASH_MISMATCH")
        if manifest.calibration_sha256 != file_sha256(calibration_path):
            failures.append("CALIBRATION_HASH_MISMATCH")
        if manifest.candidate_manifest_hash != evaluation_candidate_manifest_hash:
            failures.append("CANDIDATE_MANIFEST_MISMATCH")
        if manifest.decision_context_version != evaluation_decision_context_version:
            failures.append("DECISION_CONTEXT_MISMATCH")
        if manifest.alert_lineage_hash != evaluation_alert_lineage_hash:
            failures.append("ALERT_LINEAGE_MISMATCH")
        if manifest.eligibility_mask_hash != evaluation_eligibility_mask_hash:
            failures.append("ELIGIBILITY_MASK_MISMATCH")
        if failures:
            raise ValueError(f"model bundle validation failed: {','.join(failures)}")

    def register(
        self, manifest: ModelBundleManifest, model_path: Path, calibration_path: Path
    ) -> Path:
        destination = self.root / manifest.bundle_id
        if destination.exists():
            raise ValueError("model bundle identifier is immutable")
        destination.mkdir(parents=True)
        model_destination = destination / "model.ubj"
        calibration_destination = destination / "calibration.json"
        model_destination.write_bytes(model_path.read_bytes())
        calibration_destination.write_bytes(calibration_path.read_bytes())
        (destination / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return destination
