"""Verified, network-free held-out replay repository and scorer."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
from arrive90_evaluation.final_metrics import predict_final_bundle
from arrive90_features.transform import FeatureTransformInput, FeatureValue, FittedFeatureTransform
from arrive90_models.predictive_bundle import AftPredictiveBundle
from arrive90_outcomes.travel_time_baselines import (
    EmpiricalMidpointBaseline,
    EmpiricalMidpointQuery,
    three_hour_bucket,
)

DEFAULT_DEMO_ROOT = Path("artifacts/demo/travel-time-v1")
DEFAULT_FINAL_REPORT = Path("artifacts/reports/final/travel-time-v1.2.json")
DEFAULT_CLAIMS = Path("artifacts/reports/claims/travel-time-v1.2.json")
HORIZONS = (300, 600, 900, 1200, 1800, 2700, 3600)
QUANTILES = (0.5, 0.8, 0.9)
STATION_NAMES = {
    "70038": "Bowdoin",
    "70039": "Government Center",
    "70040": "Government Center",
    "70041": "State",
    "70042": "State",
    "70043": "Aquarium",
    "70044": "Aquarium",
    "70045": "Maverick",
    "70046": "Maverick",
    "70047": "Airport",
    "70048": "Airport",
    "70049": "Wood Island",
    "70050": "Wood Island",
    "70051": "Orient Heights",
    "70052": "Orient Heights",
    "70053": "Suffolk Downs",
    "70054": "Suffolk Downs",
    "70055": "Beachmont",
    "70056": "Beachmont",
    "70057": "Revere Beach",
    "70058": "Revere Beach",
    "70059": "Wonderland",
    "70060": "Wonderland",
    "70838": "Wonderland",
}


class ExplorerArtifactError(ValueError):
    """A committed explorer artifact is absent, inconsistent, or corrupted."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExplorerArtifactError(f"explorer artifact unavailable: {path}") from error
    if not isinstance(payload, dict):
        raise ExplorerArtifactError(f"explorer artifact must be an object: {path}")
    return payload


def _transform(payload: dict[str, Any]) -> FittedFeatureTransform:
    vocabularies = payload.get("categorical_vocabularies")
    columns = payload.get("column_names")
    if not isinstance(vocabularies, dict) or not isinstance(columns, list):
        raise ExplorerArtifactError("feature transform artifact is incomplete")
    try:
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
            column_names=tuple(str(value) for value in columns),
            output_schema_sha256=str(payload["output_schema_sha256"]),
            csr_index_dtype=str(payload["csr_index_dtype"]),
            value_dtype=str(payload["value_dtype"]),
            version=str(payload["version"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ExplorerArtifactError("feature transform artifact failed validation") from error


def _station(stop_id: str) -> dict[str, str]:
    return {"name": STATION_NAMES.get(stop_id, f"Stop {stop_id}"), "stop_id": stop_id}


def _float(value: FeatureValue, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ExplorerArtifactError(f"replay feature {name} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ExplorerArtifactError(f"replay feature {name} is not finite")
    return result


def _local_hour(values: dict[str, FeatureValue]) -> int:
    sine = _float(values["local_time_sin"], "local_time_sin")
    cosine = _float(values["local_time_cos"], "local_time_cos")
    angle = math.atan2(sine, cosine)
    if angle < 0:
        angle += 2 * math.pi
    return min(23, int(angle * 24 / (2 * math.pi)))


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    replay_id: str
    payload: dict[str, Any]

    @property
    def feature_values(self) -> dict[str, FeatureValue]:
        raw = self.payload["feature_payload"]["registered_values"]
        return {str(key): cast(FeatureValue, value) for key, value in raw.items()}

    @property
    def origin_stop_id(self) -> str:
        return str(self.feature_values["origin_stop_id"])

    @property
    def destination_stop_id(self) -> str:
        return str(self.feature_values["destination_stop_id"])

    @property
    def direction_id(self) -> str:
        return str(self.feature_values["direction_id"])


@dataclass(frozen=True, slots=True)
class ExplorerRepository:
    demo_root: Path
    report: dict[str, Any]
    claims: dict[str, Any]
    fixture: dict[str, Any]
    selection: dict[str, Any]
    station_coordinates: dict[str, Any]
    transform: FittedFeatureTransform
    transform_sha256: str
    bundle: AftPredictiveBundle
    baseline: EmpiricalMidpointBaseline
    baseline_sha256: str
    records: dict[str, ReplayRecord]

    @classmethod
    def load(
        cls,
        demo_root: Path = DEFAULT_DEMO_ROOT,
        *,
        final_report_path: Path = DEFAULT_FINAL_REPORT,
        claims_path: Path = DEFAULT_CLAIMS,
    ) -> ExplorerRepository:
        assets = _load_json(demo_root / "explorer-assets.json")
        fixture_path = demo_root / "replay-fixture.json"
        selection_path = demo_root / "replay-selection.json"
        coordinates_path = demo_root / "station-coordinates.json"
        fixture = _load_json(fixture_path)
        selection = _load_json(selection_path)
        coordinates = _load_json(coordinates_path)
        report = _load_json(final_report_path)
        claims = _load_json(claims_path)
        transform_path = demo_root / str(assets.get("feature_transform_path", ""))
        baseline_path = demo_root / str(assets.get("empirical_baseline_path", ""))
        transform_sha256 = _sha256(transform_path)
        baseline_sha256 = _sha256(baseline_path)
        if (
            transform_sha256 != assets.get("feature_transform_sha256")
            or baseline_sha256 != assets.get("empirical_baseline_sha256")
            or transform_sha256
            != fixture.get("replays", [{}])[0]
            .get("feature_payload", {})
            .get("feature_schema_sha256")
            or _sha256(coordinates_path) != fixture.get("station_coordinate_artifact_sha256")
        ):
            raise ExplorerArtifactError("explorer feature or baseline lineage failed verification")
        fitted_transform = _transform(_load_json(transform_path))
        model_directories = sorted((demo_root / "model").glob("*"))
        if len(model_directories) != 1 or not model_directories[0].is_dir():
            raise ExplorerArtifactError("exactly one allow-listed model bundle is required")
        try:
            bundle = AftPredictiveBundle.load(
                model_directories[0], full_feature_count=len(fitted_transform.column_names)
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ExplorerArtifactError("allow-listed model bundle failed validation") from error
        if bundle.manifest.manifest_hash != fixture.get(
            "model_manifest_sha256"
        ) or bundle.manifest.manifest_hash != report.get("demo_artifacts", {}).get(
            "bundle_manifest_sha256"
        ):
            raise ExplorerArtifactError("model identity does not match final evaluation")
        baseline_payload = _load_json(baseline_path).get("empirical_midpoint")
        if not isinstance(baseline_payload, dict) or not isinstance(
            baseline_payload.get("manifest"), dict
        ):
            raise ExplorerArtifactError("empirical midpoint artifact is incomplete")
        baseline = EmpiricalMidpointBaseline.from_manifest(baseline_payload["manifest"])
        if baseline.manifest_sha256 != baseline_payload.get("manifest_sha256"):
            raise ExplorerArtifactError("empirical midpoint artifact hash is invalid")
        raw_replays = fixture.get("replays")
        if not isinstance(raw_replays, list) or len(raw_replays) != 200:
            raise ExplorerArtifactError("held-out replay fixture must contain 200 rows")
        records: dict[str, ReplayRecord] = {}
        for raw in raw_replays:
            if not isinstance(raw, dict):
                raise ExplorerArtifactError("held-out replay row must be an object")
            replay_id = str(raw.get("replay_id", ""))
            if not replay_id or replay_id in records:
                raise ExplorerArtifactError("held-out replay identifiers must be unique")
            if "outcome_reveal" in raw.get("feature_payload", {}):
                raise ExplorerArtifactError("outcome data entered a replay feature payload")
            records[replay_id] = ReplayRecord(replay_id, raw)
        return cls(
            demo_root,
            report,
            claims,
            fixture,
            selection,
            coordinates,
            fitted_transform,
            transform_sha256,
            bundle,
            baseline,
            baseline_sha256,
            records,
        )

    def metadata(self) -> dict[str, Any]:
        promoted = self.report["models"][self.bundle.manifest.bundle_id]
        point = self.report["point_diagnostics"]
        return {
            "acceptance_version": self.fixture["acceptance_version"],
            "artifact_hashes": {
                "claim_registry": self.claims["final_report_sha256"],
                "feature_transform": self.transform_sha256,
                "model_manifest": self.bundle.manifest.manifest_hash,
                "replay_fixture": self.report["demo_artifacts"]["fixture_sha256"],
                "replay_selection": self.fixture["replay_selection_manifest_sha256"],
            },
            "attribution": (
                "Historical MBTA-derived observations from the Cornell Tech Bus Observatory, "
                "used under CC BY-NC 4.0 with MassDOT source attribution."
            ),
            "evidence_version": self.report["version"],
            "final_test": self.report["final_test"],
            "limitations": [
                "Historical Blue Line replays only; this is not a live arrival product.",
                "Outcomes may be interval-censored, right-censored, or unavailable.",
                "Point comparisons exclude rows without a finite upper outcome bound.",
                "The replay sample is outcome-blind and is not a random performance estimate.",
            ],
            "model": {
                "bundle_id": self.bundle.manifest.bundle_id,
                "distribution": self.bundle.manifest.aft_distribution,
                "interval_negative_log_likelihood": promoted["interval_negative_log_likelihood"],
                "scale": self.bundle.manifest.aft_scale,
            },
            "point_results": point["comparisons"],
            "point_diagnostics": point,
            "replay_count": len(self.records),
            "retained_lines": [{"line_id": "Blue", "name": "Blue Line"}],
            "split": self.fixture["split"],
            "version": "travel-time-replay-explorer-v1",
        }

    def lines(self) -> dict[str, Any]:
        return {
            "lines": [{"line_id": "Blue", "name": "Blue Line"}],
            "split": self.fixture["split"],
        }

    def stations(self) -> dict[str, Any]:
        stop_ids = sorted(
            {record.origin_stop_id for record in self.records.values()}
            | {record.destination_stop_id for record in self.records.values()}
        )
        return {"line_id": "Blue", "stations": [_station(stop_id) for stop_id in stop_ids]}

    def inventory(
        self,
        *,
        line_id: str = "Blue",
        direction_id: str | None = None,
        origin_stop_id: str | None = None,
        destination_stop_id: str | None = None,
    ) -> dict[str, Any]:
        if line_id != "Blue":
            raise ValueError("Blue is the only retained line in travel-time-v1")
        matching = [
            record
            for record in self.records.values()
            if (direction_id is None or record.direction_id == direction_id)
            and (origin_stop_id is None or record.origin_stop_id == origin_stop_id)
            and (destination_stop_id is None or record.destination_stop_id == destination_stop_id)
        ]
        matching.sort(key=lambda item: (item.payload["service_date"], item.replay_id))
        origins = sorted({record.origin_stop_id for record in self.records.values()})
        destinations = sorted({record.destination_stop_id for record in self.records.values()})
        return {
            "directions": sorted({record.direction_id for record in self.records.values()}),
            "destinations": [_station(stop_id) for stop_id in destinations],
            "filters": {
                "destination_stop_id": destination_stop_id,
                "direction_id": direction_id,
                "line_id": line_id,
                "origin_stop_id": origin_stop_id,
            },
            "origins": [_station(stop_id) for stop_id in origins],
            "replays": [self._inventory_row(record) for record in matching],
            "split": self.fixture["split"],
        }

    def _inventory_row(self, record: ReplayRecord) -> dict[str, Any]:
        values = record.feature_values
        return {
            "destination": _station(record.destination_stop_id),
            "direction_id": record.direction_id,
            "line_id": str(values["route_id"]),
            "origin": _station(record.origin_stop_id),
            "replay_id": record.replay_id,
            "scheduled_remaining_seconds": _float(
                values["scheduled_remaining_seconds"], "scheduled_remaining_seconds"
            ),
            "service_date": record.payload["service_date"],
            "source_example_sha256": record.payload["source_example_sha256"],
        }

    def _record(self, replay_id: str) -> ReplayRecord:
        try:
            return self.records[replay_id]
        except KeyError as error:
            raise KeyError(f"unknown held-out replay: {replay_id}") from error

    def prediction(self, replay_id: str, *, horizon_seconds: int) -> dict[str, Any]:
        if horizon_seconds not in HORIZONS:
            raise ValueError("horizon must be one of 300, 600, 900, 1200, 1800, 2700, 3600")
        record = self._record(replay_id)
        values = record.feature_values
        coordinate_key = str(record.payload["feature_payload"]["station_coordinate_key"])
        coordinate = self.station_coordinates.get("coordinates", {}).get(coordinate_key)
        if not isinstance(coordinate, dict):
            raise ExplorerArtifactError("public station coordinate is unavailable for replay")
        values["anchor_latitude"] = float(coordinate["latitude"])
        values["anchor_longitude"] = float(coordinate["longitude"])
        matrix = self.transform.transform((FeatureTransformInput(replay_id, values),))
        scored = predict_final_bundle(
            self.bundle,
            matrix,
            horizons_seconds=HORIZONS,
            quantiles=QUANTILES,
            model_horizon_seconds=3600,
        )
        expected = record.payload["offline_prediction"]
        maximum_delta = max(
            abs(float(scored.raw_margins[0]) - float(expected["raw_margin"])),
            float(
                np.max(
                    np.abs(
                        scored.probabilities[0]
                        - np.asarray(expected["probabilities"], dtype=np.float64)
                    )
                )
            ),
        )
        if maximum_delta > 1e-12:
            raise ExplorerArtifactError("online replay score differs from frozen offline score")
        query = EmpiricalMidpointQuery(
            anchor_id=replay_id,
            route_id=str(values["route_id"]),
            direction_id=record.direction_id,
            origin_stop_id=record.origin_stop_id,
            destination_stop_id=record.destination_stop_id,
            destination_offset=int(
                _float(values["remaining_scheduled_stop_count"], "remaining_scheduled_stop_count")
            ),
            day_type=str(record.payload["slices"]["day_type"]),
            time_bucket=three_hour_bucket(_local_hour(values)),
        )
        empirical = self.baseline.predict(query)
        horizon_index = HORIZONS.index(horizon_seconds)
        quantiles = []
        for index, probability in enumerate(QUANTILES):
            resolved = bool(scored.quantiles_resolved[0, index])
            quantiles.append(
                {
                    "level": f"p{int(probability * 100)}",
                    "resolved_within_60_minutes": resolved,
                    "seconds": float(scored.quantiles_seconds[0, index]) if resolved else None,
                }
            )
        cutoff_history = {
            "anchor_bearing": values["anchor_bearing"],
            "anchor_speed": values["anchor_speed"],
            "cutoff": "ANCHOR_OBSERVATION",
            "elapsed_episode_seconds": values["elapsed_episode_seconds"],
            "median_last_three_segment_seconds": values["median_last_three_segment_seconds"],
            "most_recent_observation_gap_seconds": values["most_recent_observation_gap_seconds"],
            "observed_origin_lateness_seconds": values["observed_origin_lateness_seconds"],
            "observed_stops_before_anchor": values["observed_stops_before_anchor"],
            "previous_stopped_segment_seconds": values["previous_stopped_segment_seconds"],
            "service_date": record.payload["service_date"],
        }
        return {
            "baselines": {
                "empirical_midpoint": {
                    "backoff_level": empirical.backoff_level,
                    "manifest_sha256": self.baseline.manifest_sha256,
                    "seconds": empirical.seconds,
                },
                "official_schedule": {
                    "seconds": _float(
                        values["scheduled_remaining_seconds"],
                        "scheduled_remaining_seconds",
                    )
                },
            },
            "cutoff_visible_history": cutoff_history,
            "evidence_version": self.report["version"],
            "fixed_horizon_probabilities": [
                {"probability": float(scored.probabilities[0, index]), "seconds": horizon}
                for index, horizon in enumerate(HORIZONS)
            ],
            "lineage": {
                "feature_transform_sha256": self.transform_sha256,
                "model_manifest_sha256": self.bundle.manifest.manifest_hash,
                "replay_selection_sha256": self.fixture["replay_selection_manifest_sha256"],
                "source_example_sha256": record.payload["source_example_sha256"],
            },
            "model": {
                "bundle_id": self.bundle.manifest.bundle_id,
                "distribution": self.bundle.manifest.aft_distribution,
                "raw_margin": float(scored.raw_margins[0]),
                "scale": self.bundle.manifest.aft_scale,
            },
            "outcome_data_available_to_scorer": False,
            "quantiles": quantiles,
            "replay": self._inventory_row(record),
            "selected_horizon": {
                "probability": float(scored.probabilities[0, horizon_index]),
                "seconds": horizon_seconds,
            },
            "split": self.fixture["split"],
        }

    def reveal(self, replay_id: str) -> dict[str, Any]:
        record = self._record(replay_id)
        reveal = record.payload.get("outcome_reveal")
        if not isinstance(reveal, dict):
            raise ExplorerArtifactError("outcome reveal is unavailable for replay")
        return {
            "evidence_version": self.report["version"],
            "observed_after_cutoff": True,
            "outcome": reveal,
            "replay_id": replay_id,
            "split": self.fixture["split"],
        }

    def reliability(self, *, horizon_seconds: int) -> dict[str, Any]:
        if horizon_seconds not in HORIZONS:
            raise ValueError("unsupported reliability horizon")
        rows = self.report["calibration"][self.bundle.manifest.bundle_id]
        return next(item for item in rows if item["horizon_seconds"] == horizon_seconds)

    def evidence(self) -> dict[str, Any]:
        return {
            "availability": self.report["availability"],
            "claims": self.claims["claims"],
            "drift": self.report["drift"],
            "failure_cases": self.report["failure_cases"],
            "methodology": {
                "bootstrap": self.report["bootstrap"],
                "metric_definitions": self.report["metric_definitions"],
            },
            "negative_results": self.report["negative_results"],
            "report_sha256": self.claims["final_report_sha256"],
        }
