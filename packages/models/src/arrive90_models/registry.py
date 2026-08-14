"""Immutable, lineage-complete registry for travel-time predictive bundles."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def value_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelBundleManifest:
    bundle_id: str
    acceptance_version: str
    model_schema_version: str
    bundle_kind: str
    aft_distribution: str
    aft_scale: float
    model_config_sha256: str
    source_lock_sha256: str
    normalized_manifest_sha256: str
    dataset_manifest_sha256: str
    unsampled_manifest_sha256: str
    split_manifest_sha256: str
    training_row_manifest_sha256: str
    validation_row_manifest_sha256: str
    calibration_row_manifest_sha256: str
    feature_registry_sha256: str
    feature_transform_sha256: str
    full_feature_order_sha256: str
    model_feature_names: tuple[str, ...]
    model_feature_indices: tuple[int, ...]
    model_sha256: str
    model_wrapper_manifest_sha256: str
    calibrator_sha256: str
    dependency_lock_sha256: str
    code_sha256: str
    xgboost_version: str
    numpy_version: str
    scipy_version: str
    random_seed: int
    xgboost_thread_count: int
    final_test_outcomes_opened: bool

    def __post_init__(self) -> None:
        if not self.bundle_id or self.acceptance_version != "travel-time-v1.2":
            raise ValueError("model bundle identity is invalid")
        if self.model_schema_version != "travel-time-predictive-bundle-v1":
            raise ValueError("model bundle schema version is invalid")
        if self.final_test_outcomes_opened:
            raise ValueError("Milestone 3 model bundles cannot open final-test outcomes")
        hashes = (
            self.model_config_sha256,
            self.source_lock_sha256,
            self.normalized_manifest_sha256,
            self.dataset_manifest_sha256,
            self.unsampled_manifest_sha256,
            self.split_manifest_sha256,
            self.training_row_manifest_sha256,
            self.validation_row_manifest_sha256,
            self.calibration_row_manifest_sha256,
            self.feature_registry_sha256,
            self.feature_transform_sha256,
            self.full_feature_order_sha256,
            self.model_sha256,
            self.model_wrapper_manifest_sha256,
            self.calibrator_sha256,
            self.dependency_lock_sha256,
            self.code_sha256,
        )
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in hashes
        ):
            raise ValueError("model bundle lineage fields must be SHA-256 digests")
        if len(set(self.model_feature_names)) != len(self.model_feature_names):
            raise ValueError("model feature names must be unique")
        if self.bundle_kind == "INTERCEPT_ONLY":
            if self.model_feature_names != ("intercept",) or self.model_feature_indices:
                raise ValueError("intercept-only bundle feature mapping is invalid")
        elif len(self.model_feature_names) != len(self.model_feature_indices):
            raise ValueError("model feature names and indices must align")
        if tuple(sorted(set(self.model_feature_indices))) != self.model_feature_indices:
            raise ValueError("model feature indices must be unique and increasing")
        if self.xgboost_thread_count != 1:
            raise ValueError("qualification bundles require one XGBoost thread")

    @property
    def manifest_hash(self) -> str:
        return value_sha256(asdict(self))

    @property
    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ModelBundleManifest:
        converted = dict(payload)
        converted["model_feature_names"] = tuple(converted["model_feature_names"])
        converted["model_feature_indices"] = tuple(converted["model_feature_indices"])
        return cls(**converted)


@dataclass(frozen=True, slots=True)
class RegistryExpectations:
    acceptance_version: str
    source_lock_sha256: str
    normalized_manifest_sha256: str
    dataset_manifest_sha256: str
    unsampled_manifest_sha256: str
    split_manifest_sha256: str
    feature_registry_sha256: str
    feature_transform_sha256: str
    full_feature_order_sha256: str
    dependency_lock_sha256: str
    code_sha256: str


class ModelRegistry:
    """Validate and immutably copy predictive bundles into a hash-named store."""

    def __init__(
        self,
        root: Path,
        *,
        expectations: RegistryExpectations,
        promoted_bundle_size_bytes_max: int = 10 * 1024 * 1024,
    ) -> None:
        if promoted_bundle_size_bytes_max <= 0:
            raise ValueError("model bundle size budget must be positive")
        self.root = root
        self.expectations = expectations
        self.promoted_bundle_size_bytes_max = promoted_bundle_size_bytes_max

    def validate(
        self,
        manifest: ModelBundleManifest,
        *,
        model_path: Path,
        model_manifest_path: Path,
        calibration_path: Path,
        promoted: bool = False,
    ) -> None:
        failures: list[str] = []
        expected = self.expectations
        comparisons = {
            "ACCEPTANCE_VERSION_MISMATCH": (
                manifest.acceptance_version,
                expected.acceptance_version,
            ),
            "SOURCE_LOCK_MISMATCH": (manifest.source_lock_sha256, expected.source_lock_sha256),
            "NORMALIZED_MANIFEST_MISMATCH": (
                manifest.normalized_manifest_sha256,
                expected.normalized_manifest_sha256,
            ),
            "DATASET_MANIFEST_MISMATCH": (
                manifest.dataset_manifest_sha256,
                expected.dataset_manifest_sha256,
            ),
            "UNSAMPLED_MANIFEST_MISMATCH": (
                manifest.unsampled_manifest_sha256,
                expected.unsampled_manifest_sha256,
            ),
            "SPLIT_MANIFEST_MISMATCH": (
                manifest.split_manifest_sha256,
                expected.split_manifest_sha256,
            ),
            "FEATURE_REGISTRY_MISMATCH": (
                manifest.feature_registry_sha256,
                expected.feature_registry_sha256,
            ),
            "FEATURE_TRANSFORM_MISMATCH": (
                manifest.feature_transform_sha256,
                expected.feature_transform_sha256,
            ),
            "FEATURE_ORDER_MISMATCH": (
                manifest.full_feature_order_sha256,
                expected.full_feature_order_sha256,
            ),
            "DEPENDENCY_LOCK_MISMATCH": (
                manifest.dependency_lock_sha256,
                expected.dependency_lock_sha256,
            ),
            "CODE_MISMATCH": (manifest.code_sha256, expected.code_sha256),
        }
        failures.extend(name for name, values in comparisons.items() if values[0] != values[1])
        if not model_path.is_file() or manifest.model_sha256 != file_sha256(model_path):
            failures.append("MODEL_HASH_MISMATCH")
        if (
            not model_manifest_path.is_file()
            or manifest.model_wrapper_manifest_sha256 != file_sha256(model_manifest_path)
        ):
            failures.append("MODEL_MANIFEST_HASH_MISMATCH")
        if not calibration_path.is_file() or manifest.calibrator_sha256 != file_sha256(
            calibration_path
        ):
            failures.append("CALIBRATOR_HASH_MISMATCH")
        if promoted:
            size = (
                model_path.stat().st_size
                + model_manifest_path.stat().st_size
                + calibration_path.stat().st_size
                + len(canonical_json(manifest.payload))
            )
            if size > self.promoted_bundle_size_bytes_max:
                failures.append("PROMOTED_BUNDLE_SIZE_EXCEEDED")
        if failures:
            raise ValueError(f"model bundle validation failed: {','.join(sorted(failures))}")

    def register(
        self,
        manifest: ModelBundleManifest,
        model_path: Path,
        model_manifest_path: Path,
        calibration_path: Path,
        *,
        promoted: bool = False,
    ) -> Path:
        self.validate(
            manifest,
            model_path=model_path,
            model_manifest_path=model_manifest_path,
            calibration_path=calibration_path,
            promoted=promoted,
        )
        destination = self.root / manifest.manifest_hash
        if destination.exists():
            raise ValueError("model bundle manifest hash is immutable")
        destination.mkdir(parents=True)
        model_destination = destination / "model.ubj"
        model_manifest_destination = destination / "model-manifest.json"
        calibration_destination = destination / "calibration.json"
        manifest_destination = destination / "manifest.json"
        model_destination.write_bytes(model_path.read_bytes())
        model_manifest_destination.write_bytes(model_manifest_path.read_bytes())
        calibration_destination.write_bytes(calibration_path.read_bytes())
        manifest_destination.write_bytes(canonical_json(manifest.payload))
        descriptor = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.validate(
            manifest,
            model_path=model_destination,
            model_manifest_path=model_manifest_destination,
            calibration_path=calibration_destination,
            promoted=promoted,
        )
        return destination

    def load_manifest(self, manifest_hash: str) -> ModelBundleManifest:
        if len(manifest_hash) != 64:
            raise ValueError("registry manifest hash is invalid")
        path = self.root / manifest_hash / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("registry manifest must be an object")
        manifest = ModelBundleManifest.from_payload(payload)
        if manifest.manifest_hash != manifest_hash:
            raise ValueError("registry path does not match the manifest hash")
        self.validate(
            manifest,
            model_path=path.parent / "model.ubj",
            model_manifest_path=path.parent / "model-manifest.json",
            calibration_path=path.parent / "calibration.json",
        )
        return manifest
