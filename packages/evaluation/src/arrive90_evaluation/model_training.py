"""End-to-end Milestone 3 baseline, AFT, calibration, and registry workflow."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import scipy  # type: ignore[import-untyped]
import xgboost as xgb
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_ingestion.acquisition import sha256_file
from arrive90_models.calibration import (
    CALIBRATOR_VERSION,
    CalibrationCell,
    fit_sigmoid_calibrator,
)
from arrive90_models.distributions import AftDistribution
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_models.registry import (
    ModelBundleManifest,
    ModelRegistry,
    RegistryExpectations,
    canonical_json,
    file_sha256,
    value_sha256,
)
from arrive90_models.xgb_aft import AftTrainingConfig, MatrixLike, TrainedAftModel, train_aft_model
from arrive90_outcomes.travel_time_baselines import (
    EmpiricalMidpointRow,
    fit_empirical_midpoint_baseline,
    official_scheduled_remaining_seconds,
)
from scipy import sparse

from arrive90_evaluation.aft_metrics import (
    ValidationDiagnostics,
    aft_cdf_matrix,
    identified_threshold,
    validation_diagnostics,
)
from arrive90_evaluation.modeling_data import (
    ModelingSplit,
    load_modeling_context,
    load_modeling_split,
)
from arrive90_evaluation.year_dataset import DEFAULT_DATASET_ROOT, YearDatasetError

DEFAULT_CONFIG_PATH = Path("configs/models/travel-time-v1.json")
DEFAULT_MODEL_ROOT = Path("data/models/travel-time-v1/primary")
DEFAULT_RUNTIME_ROOT = Path("artifacts/runtime/milestone-3")
DEFAULT_NORMALIZED_ROOT = Path("data/normalized")
LATENCY_SELECTION_RESOLUTION_SECONDS = 0.001


@dataclass(frozen=True, slots=True)
class CandidateRun:
    identifier: str
    bundle_kind: str
    config: AftTrainingConfig
    feature_indices: tuple[int, ...]
    feature_names: tuple[str, ...]
    model: TrainedAftModel
    diagnostics: ValidationDiagnostics
    parameter_count: int
    latency_seconds: float
    fit_seconds: float

    @property
    def rank_key(self) -> tuple[float, float, float, int, int, bytes]:
        return (
            self.diagnostics.weighted_interval_negative_log_likelihood,
            self.diagnostics.weighted_horizon_brier_score,
            self.diagnostics.worst_supported_horizon_calibration_error,
            self.parameter_count,
            round(self.latency_seconds / LATENCY_SELECTION_RESOLUTION_SECONDS),
            self.identifier.encode(),
        )


@dataclass(frozen=True, slots=True)
class SavedRun:
    run: CandidateRun
    model_path: Path
    model_manifest_path: Path


@dataclass(frozen=True, slots=True)
class ModelTrainingResult:
    registry_index_path: Path
    registry_index_sha256: str
    promoted_bundle_id: str
    promoted_manifest_sha256: str
    selection_freeze_path: Path
    selection_freeze_sha256: str
    point_baseline_path: Path
    point_baseline_sha256: str
    validation_comparison_path: Path
    validation_comparison_sha256: str
    latency_report_path: Path
    runtime_report_path: Path


def _load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION
    ):
        raise YearDatasetError("modeling configuration acceptance version is invalid")
    if payload.get("version") != "travel-time-modeling-v1":
        raise YearDatasetError("modeling configuration version is invalid")
    if (
        float(payload["qualification"]["latency_selection_resolution_seconds"])
        != LATENCY_SELECTION_RESOLUTION_SECONDS
    ):
        raise YearDatasetError("modeling latency-selection resolution is invalid")
    return payload


def _write_content_addressed_json(root: Path, stem: str, payload: object) -> tuple[Path, str]:
    body = canonical_json(payload)
    digest = hashlib.sha256(body).hexdigest()
    path = root / f"{stem}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise YearDatasetError(f"content-addressed model artifact differs: {path}")
    if not path.exists():
        path.write_bytes(body)
    return path, digest


def _write_runtime_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _code_sha256(config_path: Path) -> str:
    root = Path(__file__).resolve().parents[4]
    relative_paths = (
        config_path,
        Path("packages/evaluation/src/arrive90_evaluation/aft_metrics.py"),
        Path("packages/evaluation/src/arrive90_evaluation/modeling_data.py"),
        Path("packages/evaluation/src/arrive90_evaluation/model_training.py"),
        Path("packages/models/src/arrive90_models/calibration.py"),
        Path("packages/models/src/arrive90_models/distributions.py"),
        Path("packages/models/src/arrive90_models/predictive_bundle.py"),
        Path("packages/models/src/arrive90_models/registry.py"),
        Path("packages/models/src/arrive90_models/xgb_aft.py"),
        Path("packages/outcomes/src/arrive90_outcomes/travel_time_baselines.py"),
    )
    digest = hashlib.sha256()
    for relative in sorted(relative_paths, key=lambda value: value.as_posix().encode()):
        path = root / relative
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _raw_feature_indices(
    full_names: tuple[str, ...], registry_features: set[str]
) -> tuple[int, ...]:
    indices: list[int] = []
    for index, column_name in enumerate(full_names):
        raw_name = column_name.split("=", 1)[0] if "=" in column_name else column_name
        if raw_name in registry_features:
            indices.append(index)
    return tuple(indices)


def _bundle_features(
    kind: str, full_names: tuple[str, ...], config: dict[str, Any]
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    if kind == "INTERCEPT_ONLY":
        return (), ("intercept",)
    if kind == "FULL":
        indices = tuple(range(len(full_names)))
        return indices, full_names
    bundles = config.get("feature_bundles")
    if not isinstance(bundles, dict) or not isinstance(bundles.get(kind), dict):
        raise YearDatasetError(f"unknown frozen feature bundle: {kind}")
    specification = bundles[kind]
    if "include_registry_features" in specification:
        raw = {str(value) for value in specification["include_registry_features"]}
    else:
        excluded = {str(value) for value in specification["exclude_registry_features"]}
        raw = set(TRAVEL_TIME_V1_REGISTRY.specs) - excluded
    unknown = raw - set(TRAVEL_TIME_V1_REGISTRY.specs)
    if unknown:
        raise YearDatasetError(
            f"feature bundle contains unknown registry features: {sorted(unknown)}"
        )
    indices = _raw_feature_indices(full_names, raw)
    if not indices:
        raise YearDatasetError(f"feature bundle resolved to no columns: {kind}")
    return indices, tuple(full_names[index] for index in indices)


def _select_features(matrix: sparse.csr_matrix, kind: str, indices: tuple[int, ...]) -> MatrixLike:
    if kind == "INTERCEPT_ONLY":
        return sparse.csr_matrix(np.ones((matrix.shape[0], 1), dtype=np.float32))
    return matrix[:, list(indices)]


def _parameter_count(model: TrainedAftModel) -> int:
    def count_nodes(node: dict[str, Any]) -> int:
        children = node.get("children", [])
        return 1 + sum(count_nodes(child) for child in children)

    return sum(count_nodes(json.loads(tree)) for tree in model.booster.get_dump(dump_format="json"))


def _latency_seconds(
    model: TrainedAftModel,
    features: MatrixLike,
    *,
    warmups: int,
    repetitions: int,
) -> float:
    for _ in range(warmups):
        model.predict_raw_margin(features)
    timings: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        model.predict_raw_margin(features)
        timings.append((time.perf_counter_ns() - started) / 1_000_000_000)
    return statistics.median(timings)


def _train_run(
    *,
    identifier: str,
    bundle_kind: str,
    aft_config: AftTrainingConfig,
    train: ModelingSplit,
    validation: ModelingSplit,
    full_names: tuple[str, ...],
    model_config: dict[str, Any],
    lineage: dict[str, str],
) -> CandidateRun:
    indices, feature_names = _bundle_features(bundle_kind, full_names, model_config)
    train_features = _select_features(train.features, bundle_kind, indices)
    validation_features = _select_features(validation.features, bundle_kind, indices)
    started = time.monotonic()
    model = train_aft_model(
        train_features,
        train.lower_bounds,
        train.upper_bounds,
        train.analysis_weights,
        feature_names,
        aft_config,
        lineage={**lineage, "bundle_kind": bundle_kind},
    )
    fit_seconds = time.monotonic() - started
    margins = model.predict_raw_margin(validation_features)
    horizons = tuple(int(value) for value in model_config["calibration"]["horizons_seconds"])
    diagnostics = validation_diagnostics(
        example_ids=validation.example_ids,
        anchor_ids=validation.anchor_ids,
        raw_margins=margins,
        lower_bounds=validation.lower_bounds,
        upper_bounds=validation.upper_bounds,
        weights=validation.analysis_weights,
        horizons_seconds=horizons,
        scale=aft_config.scale,
        distribution=aft_config.distribution,
    )
    qualification = model_config["qualification"]
    sample_size = min(int(qualification["latency_sample_rows"]), validation.features.shape[0])
    latency = _latency_seconds(
        model,
        validation_features[:sample_size],
        warmups=int(qualification["latency_warmup_repetitions"]),
        repetitions=int(qualification["latency_repetitions"]),
    )
    return CandidateRun(
        identifier,
        bundle_kind,
        aft_config,
        indices,
        feature_names,
        model,
        diagnostics,
        _parameter_count(model),
        latency,
        fit_seconds,
    )


def _diagnostic_payload(run: CandidateRun, *, include_latency: bool) -> dict[str, Any]:
    payload = {
        "aft_config": run.config.manifest,
        "bundle_kind": run.bundle_kind,
        "feature_count": len(run.feature_names),
        "identifier": run.identifier,
        "parameter_count": run.parameter_count,
        "validation": asdict(run.diagnostics),
    }
    if include_latency:
        payload["fit_seconds"] = run.fit_seconds
        payload["latency_seconds"] = run.latency_seconds
    return payload


def _distance_to_interval(point: float, lower: float, upper: float) -> float:
    if point < lower:
        return lower - point
    if point > upper:
        return point - upper
    return 0.0


def _fit_point_baselines(
    train: ModelingSplit,
    validation: ModelingSplit,
    config: dict[str, Any],
) -> dict[str, Any]:
    baseline_config = config["baseline"]
    rows: list[EmpiricalMidpointRow] = []
    for index, metadata in enumerate(train.metadata):
        upper = float(train.upper_bounds[index])
        if not math.isfinite(upper):
            continue
        query = metadata.query
        rows.append(
            EmpiricalMidpointRow(
                anchor_id=query.anchor_id,
                route_id=query.route_id,
                direction_id=query.direction_id,
                origin_stop_id=query.origin_stop_id,
                destination_stop_id=query.destination_stop_id,
                destination_offset=query.destination_offset,
                day_type=query.day_type,
                time_bucket=query.time_bucket,
                example_id=metadata.example_id,
                midpoint_seconds=(float(train.lower_bounds[index]) + upper) / 2.0,
                analysis_weight=float(train.analysis_weights[index]),
            )
        )
    empirical = fit_empirical_midpoint_baseline(
        tuple(rows),
        minimum_finite_examples=int(baseline_config["minimum_finite_examples"]),
        minimum_distinct_anchors=int(baseline_config["minimum_distinct_anchors"]),
    )
    schedule_distance = 0.0
    empirical_distance = 0.0
    finite_weight = 0.0
    empirical_weight = 0.0
    empirical_count = 0
    for index, metadata in enumerate(validation.metadata):
        upper = float(validation.upper_bounds[index])
        if not math.isfinite(upper):
            continue
        lower = float(validation.lower_bounds[index])
        weight = float(validation.analysis_weights[index])

        schedule = official_scheduled_remaining_seconds(metadata.scheduled_remaining_seconds)
        schedule_distance += weight * _distance_to_interval(schedule, lower, upper)
        finite_weight += weight
        prediction = empirical.predict(metadata.query)
        if prediction.seconds is not None:
            empirical_distance += weight * _distance_to_interval(prediction.seconds, lower, upper)
            empirical_weight += weight
            empirical_count += 1
    if finite_weight <= 0:
        raise YearDatasetError("point baselines have no finite validation support")
    return {
        "empirical_midpoint": {
            "coverage_finite_row_count": empirical_count,
            "coverage_finite_weight": empirical_weight,
            "coverage_loss_weight": finite_weight - empirical_weight,
            "manifest": empirical.manifest,
            "manifest_sha256": empirical.manifest_sha256,
            "weighted_mean_interval_distance_seconds": (
                empirical_distance / empirical_weight if empirical_weight else None
            ),
        },
        "fitting_split": DatasetSplit.TRAINING.value,
        "official_schedule": {
            "validation_finite_weight": finite_weight,
            "weighted_mean_interval_distance_seconds": schedule_distance / finite_weight,
        },
        "validation_split": DatasetSplit.MODEL_VALIDATION.value,
        "version": "travel-time-point-baselines-v1",
    }


def _calibration_cells(
    run: CandidateRun,
    calibration: ModelingSplit,
    horizons: tuple[int, ...],
) -> tuple[tuple[CalibrationCell, ...], dict[str, Any]]:
    model_features = _select_features(calibration.features, run.bundle_kind, run.feature_indices)
    margins = run.model.predict_raw_margin(model_features)
    probabilities = aft_cdf_matrix(
        margins,
        horizons,
        scale=run.config.scale,
        distribution=run.config.distribution,
    )
    cells: list[CalibrationCell] = []
    identified_by_horizon = {str(horizon): 0 for horizon in horizons}
    for row_index in range(len(margins)):
        identified: list[tuple[int, bool]] = []
        for horizon_index, horizon in enumerate(horizons):
            outcome = identified_threshold(
                float(calibration.lower_bounds[row_index]),
                float(calibration.upper_bounds[row_index]),
                horizon,
            )
            if outcome is not None:
                identified.append((horizon_index, outcome))
                identified_by_horizon[str(horizon)] += 1
        if not identified:
            continue
        cell_weight = float(calibration.analysis_weights[row_index]) / len(identified)
        for horizon_index, outcome in identified:
            cells.append(
                CalibrationCell(
                    float(probabilities[row_index, horizon_index]), outcome, cell_weight
                )
            )
    if not cells:
        raise YearDatasetError(f"{run.identifier} has no identified calibration cells")
    return tuple(cells), {
        "identified_cell_count": len(cells),
        "identified_row_count_by_horizon": identified_by_horizon,
        "row_weight_divided_by_identified_horizon_count": True,
    }


def _save_unregistered_runs(
    runs: tuple[CandidateRun, ...], runtime_root: Path
) -> dict[str, SavedRun]:
    saved: dict[str, SavedRun] = {}
    for run in runs:
        directory = runtime_root / "unregistered" / run.identifier
        model_path = directory / "model.ubj"
        model_manifest_path = directory / "model-manifest.json"
        run.model.save(model_path, model_manifest_path)
        saved[run.identifier] = SavedRun(run, model_path, model_manifest_path)
    return saved


def train_model_registry(
    *,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    config_path: Path = DEFAULT_CONFIG_PATH,
    model_root: Path = DEFAULT_MODEL_ROOT,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> ModelTrainingResult:
    """Build all predeclared Milestone 3 bundles without opening final-test outcomes."""

    started = time.monotonic()
    config = _load_config(config_path)
    if model_root.exists() and any(model_root.iterdir()):
        raise YearDatasetError(
            f"model output root must be empty for an immutable run: {model_root}"
        )
    model_root.mkdir(parents=True, exist_ok=True)
    context = load_modeling_context(dataset_root, normalized_root=normalized_root)
    train = load_modeling_split(context, DatasetSplit.TRAINING)
    validation = load_modeling_split(context, DatasetSplit.MODEL_VALIDATION)
    full_names = context.feature_transform.column_names
    code_sha256 = _code_sha256(config_path)
    root = Path(__file__).resolve().parents[4]
    dependency_lock_sha256 = sha256_file(root / "uv.lock")
    lineage = {
        "code_sha256": code_sha256,
        "dataset_manifest_sha256": context.population_manifest_sha256,
        "dependency_lock_sha256": dependency_lock_sha256,
        "feature_transform_sha256": context.feature_transform_sha256,
        "training_row_manifest_sha256": train.row_manifest_sha256,
        "validation_row_manifest_sha256": validation.row_manifest_sha256,
    }
    grid = config["candidate_grid"]
    training_config = config["training"]
    full_runs: list[CandidateRun] = []
    for distribution_name in grid["distributions"]:
        for scale in grid["scales"]:
            distribution = AftDistribution(str(distribution_name))
            aft_config = AftTrainingConfig(
                distribution=distribution,
                scale=float(scale),
                rounds=int(grid["rounds"]),
                maximum_depth=int(grid["maximum_depth"]),
                learning_rate=float(grid["learning_rate"]),
                seed=int(training_config["seed"]),
                nthread=int(training_config["nthread"]),
                tree_method=str(training_config["tree_method"]),
                subsample=float(training_config["subsample"]),
            )
            scale_id = str(scale).replace(".", "p")
            full_runs.append(
                _train_run(
                    identifier=f"FULL-{distribution.value}-scale-{scale_id}",
                    bundle_kind="FULL",
                    aft_config=aft_config,
                    train=train,
                    validation=validation,
                    full_names=full_names,
                    model_config=config,
                    lineage=lineage,
                )
            )
    selected_full = min(full_runs, key=lambda run: run.rank_key)
    aft_baselines = tuple(
        _train_run(
            identifier=f"{kind}-{selected_full.config.distribution.value}",
            bundle_kind=kind,
            aft_config=selected_full.config,
            train=train,
            validation=validation,
            full_names=full_names,
            model_config=config,
            lineage=lineage,
        )
        for kind in ("INTERCEPT_ONLY", "SCHEDULE_CALENDAR")
    )
    strongest_baseline = min(aft_baselines, key=lambda run: run.rank_key)
    promoted = (
        selected_full
        if selected_full.rank_key < strongest_baseline.rank_key
        else strongest_baseline
    )
    ablations = tuple(
        _train_run(
            identifier=f"{kind}-{selected_full.config.distribution.value}",
            bundle_kind=kind,
            aft_config=selected_full.config,
            train=train,
            validation=validation,
            full_names=full_names,
            model_config=config,
            lineage=lineage,
        )
        for kind in config["final_comparison"]["required_ablations"]
    )
    best_by_distribution = tuple(
        min(
            (run for run in full_runs if run.config.distribution is distribution),
            key=lambda run: run.rank_key,
        )
        for distribution in AftDistribution
    )
    final_runs = tuple(
        {
            run.identifier: run for run in (*best_by_distribution, *aft_baselines, *ablations)
        }.values()
    )
    saved = _save_unregistered_runs(final_runs, runtime_root)
    point_payload = _fit_point_baselines(train, validation, config)
    point_path, point_sha256 = _write_content_addressed_json(
        model_root, "point-baselines", point_payload
    )
    comparison_payload = {
        "aft_baselines": [_diagnostic_payload(run, include_latency=False) for run in aft_baselines],
        "calibration_fit_accessed": False,
        "candidate_grid": [_diagnostic_payload(run, include_latency=False) for run in full_runs],
        "diagnostic_ablations": [
            _diagnostic_payload(run, include_latency=False) for run in ablations
        ],
        "final_compared_bundle_ids": [run.identifier for run in final_runs],
        "final_test_outcomes_opened": False,
        "predeclared_ablation_bundle_ids": [run.identifier for run in ablations],
        "promoted_bundle_id": promoted.identifier,
        "selected_full_candidate_id": selected_full.identifier,
        "strongest_aft_baseline_id": strongest_baseline.identifier,
        "selection_order": config["selection_order"],
        "scoring_latency_resolution_seconds": LATENCY_SELECTION_RESOLUTION_SECONDS,
        "training_row_manifest_sha256": train.row_manifest_sha256,
        "validation_row_manifest_sha256": validation.row_manifest_sha256,
        "version": "travel-time-validation-comparison-v1",
    }
    comparison_path, comparison_sha256 = _write_content_addressed_json(
        model_root, "validation-comparison", comparison_payload
    )
    freeze_payload = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "calibration_family_frozen": CALIBRATOR_VERSION,
        "calibration_fit_accessed": False,
        "candidate_model_hashes": {
            identifier: file_sha256(value.model_path) for identifier, value in sorted(saved.items())
        },
        "dataset_manifest_sha256": context.population_manifest_sha256,
        "feature_transform_sha256": context.feature_transform_sha256,
        "final_compared_bundle_ids": [run.identifier for run in final_runs],
        "final_test_outcomes_opened": False,
        "promoted_bundle_id": promoted.identifier,
        "validation_comparison_sha256": comparison_sha256,
        "version": "travel-time-selection-freeze-v1",
    }
    freeze_path, freeze_sha256 = _write_content_addressed_json(
        model_root, "selection-freeze", freeze_payload
    )

    calibration = load_modeling_split(context, DatasetSplit.CALIBRATION)
    horizons = tuple(int(value) for value in config["calibration"]["horizons_seconds"])
    expectations = RegistryExpectations(
        acceptance_version=DEFAULT_ACCEPTANCE_VERSION,
        source_lock_sha256=context.source_lock_sha256,
        normalized_manifest_sha256=context.normalized_manifest_sha256,
        dataset_manifest_sha256=context.population_manifest_sha256,
        unsampled_manifest_sha256=context.unsampled_manifest_sha256,
        split_manifest_sha256=context.split_manifest_sha256,
        feature_registry_sha256=TRAVEL_TIME_V1_REGISTRY.manifest_hash,
        feature_transform_sha256=context.feature_transform_sha256,
        full_feature_order_sha256=context.feature_transform.output_schema_sha256,
        dependency_lock_sha256=dependency_lock_sha256,
        code_sha256=code_sha256,
    )
    registry = ModelRegistry(
        model_root / "registry",
        expectations=expectations,
        promoted_bundle_size_bytes_max=int(config["promoted_bundle_size_bytes_max"]),
    )
    registry_entries: list[dict[str, Any]] = []
    promoted_manifest_sha256 = ""
    calibration_artifacts: dict[str, str] = {}
    for run in final_runs:
        cells, calibration_support = _calibration_cells(run, calibration, horizons)
        calibration_config = config["calibration"]
        calibrator = fit_sigmoid_calibrator(
            cells,
            maximum_iterations=int(calibration_config["maximum_iterations"]),
            ftol=float(calibration_config["ftol"]),
            gtol=float(calibration_config["gtol"]),
        )
        calibration_payload = {
            "bundle_id": run.identifier,
            "calibration_row_manifest_sha256": calibration.row_manifest_sha256,
            "calibrator": calibrator.manifest,
            "final_test_outcomes_opened": False,
            "horizons_seconds": list(horizons),
            "protocol": calibration_config,
            "selection_freeze_sha256": freeze_sha256,
            "support": calibration_support,
        }
        calibration_path, calibration_sha256 = _write_content_addressed_json(
            runtime_root / "calibration", f"calibration-{run.identifier}", calibration_payload
        )
        calibration_artifacts[run.identifier] = calibration_sha256
        saved_run = saved[run.identifier]
        model_wrapper_sha256 = file_sha256(saved_run.model_manifest_path)
        manifest = ModelBundleManifest(
            bundle_id=run.identifier,
            acceptance_version=DEFAULT_ACCEPTANCE_VERSION,
            model_schema_version="travel-time-predictive-bundle-v1",
            bundle_kind=run.bundle_kind,
            aft_distribution=run.config.distribution.value,
            aft_scale=run.config.scale,
            model_config_sha256=value_sha256(run.config.manifest),
            source_lock_sha256=context.source_lock_sha256,
            normalized_manifest_sha256=context.normalized_manifest_sha256,
            dataset_manifest_sha256=context.population_manifest_sha256,
            unsampled_manifest_sha256=context.unsampled_manifest_sha256,
            split_manifest_sha256=context.split_manifest_sha256,
            training_row_manifest_sha256=train.row_manifest_sha256,
            validation_row_manifest_sha256=validation.row_manifest_sha256,
            calibration_row_manifest_sha256=calibration.row_manifest_sha256,
            feature_registry_sha256=TRAVEL_TIME_V1_REGISTRY.manifest_hash,
            feature_transform_sha256=context.feature_transform_sha256,
            full_feature_order_sha256=context.feature_transform.output_schema_sha256,
            model_feature_names=run.feature_names,
            model_feature_indices=run.feature_indices,
            model_sha256=file_sha256(saved_run.model_path),
            model_wrapper_manifest_sha256=model_wrapper_sha256,
            calibrator_sha256=calibration_sha256,
            dependency_lock_sha256=dependency_lock_sha256,
            code_sha256=code_sha256,
            xgboost_version=xgb.__version__,
            numpy_version=np.__version__,
            scipy_version=scipy.__version__,
            random_seed=run.config.seed,
            xgboost_thread_count=run.config.nthread,
            final_test_outcomes_opened=False,
        )
        directory = registry.register(
            manifest,
            saved_run.model_path,
            saved_run.model_manifest_path,
            calibration_path,
            promoted=run.identifier == promoted.identifier,
        )
        registry_entries.append(
            {
                "bundle_id": run.identifier,
                "bundle_kind": run.bundle_kind,
                "manifest_sha256": manifest.manifest_hash,
                "registry_path": directory.relative_to(model_root).as_posix(),
            }
        )
        if run.identifier == promoted.identifier:
            promoted_manifest_sha256 = manifest.manifest_hash
    if not promoted_manifest_sha256:
        raise YearDatasetError("promoted bundle was not registered")
    promoted_directory = model_root / "registry" / promoted_manifest_sha256
    promoted_bundle = AftPredictiveBundle.load(
        promoted_directory, full_feature_count=len(full_names)
    )
    predictions = promoted_bundle.predict(validation.features[:32])
    if not predictions or any(
        prediction.probabilities != tuple(sorted(prediction.probabilities))
        for prediction in predictions
    ):
        raise YearDatasetError("promoted common bundle interface failed its scoring exercise")
    index_payload = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "calibration_artifact_sha256": dict(sorted(calibration_artifacts.items())),
        "calibration_row_manifest_sha256": calibration.row_manifest_sha256,
        "entries": sorted(registry_entries, key=lambda entry: str(entry["bundle_id"]).encode()),
        "final_test_outcomes_opened": False,
        "point_baseline_sha256": point_sha256,
        "promoted_bundle_id": promoted.identifier,
        "promoted_manifest_sha256": promoted_manifest_sha256,
        "selection_freeze_sha256": freeze_sha256,
        "validation_comparison_sha256": comparison_sha256,
        "version": "travel-time-model-registry-index-v1",
    }
    index_path, index_sha256 = _write_content_addressed_json(
        model_root, "registry-index", index_payload
    )
    latency_path = runtime_root / "latency.json"
    _write_runtime_json(
        latency_path,
        {
            "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
            "models": [_diagnostic_payload(run, include_latency=True) for run in final_runs],
            "sample_rows": min(
                int(config["qualification"]["latency_sample_rows"]),
                validation.features.shape[0],
            ),
        },
    )
    runtime_path = runtime_root / "training-run.json"
    _write_runtime_json(
        runtime_path,
        {
            "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
            "calibration_rows": calibration.features.shape[0],
            "elapsed_seconds": time.monotonic() - started,
            "final_compared_bundle_count": len(final_runs),
            "final_test_outcomes_opened": False,
            "model_root": str(model_root),
            "promoted_bundle_id": promoted.identifier,
            "promoted_manifest_sha256": promoted_manifest_sha256,
            "registry_index_sha256": index_sha256,
            "training_rows": train.features.shape[0],
            "validation_rows": validation.features.shape[0],
        },
    )
    return ModelTrainingResult(
        registry_index_path=index_path,
        registry_index_sha256=index_sha256,
        promoted_bundle_id=promoted.identifier,
        promoted_manifest_sha256=promoted_manifest_sha256,
        selection_freeze_path=freeze_path,
        selection_freeze_sha256=freeze_sha256,
        point_baseline_path=point_path,
        point_baseline_sha256=point_sha256,
        validation_comparison_path=comparison_path,
        validation_comparison_sha256=comparison_sha256,
        latency_report_path=latency_path,
        runtime_report_path=runtime_path,
    )
