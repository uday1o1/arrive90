"""Deterministic retained-line population, features, transform, and resource benchmark."""

from __future__ import annotations

import hashlib
import heapq
import hmac
import json
import math
import os
import platform
import resource
import shutil
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import scipy  # type: ignore[import-untyped]
import xgboost as xgb
from arrive90_data_contracts.dataset import DatasetSplit, chronological_split
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.realtime import require_utc
from arrive90_data_contracts.travel_time import DownstreamOutcomeState, VehicleObservation
from arrive90_features.transform import (
    MISSING_TOKEN,
    UNKNOWN_TOKEN,
    FeatureTransformInput,
    FeatureValue,
    FittedFeatureTransform,
)
from arrive90_features.travel_time import (
    ObservationCutoffView,
    build_travel_time_feature_row,
)
from arrive90_features.travel_time_registry import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    TRAVEL_TIME_V1_REGISTRY,
)
from arrive90_ingestion.acquisition import sha256_file
from arrive90_ingestion.episodes import build_trip_episodes
from arrive90_ingestion.historical_schedule import (
    ScheduleMatchReason,
    match_episodes_to_schedule,
)
from arrive90_ingestion.year_normalization import read_normalized_partition

from arrive90_evaluation.year_dataset import (
    CANDIDATE_SCHEMA,
    DEFAULT_DATASET_ROOT,
    DEFAULT_NORMALIZED_ROOT,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SCHEDULE_DATABASE,
    FinalTestOutcomeAccessError,
    YearDatasetError,
    _canonical_json,
    _load_json,
    _normalized_manifest,
    _partition_index,
    _schedule_database_hash,
    _write_atomic_json,
    _write_content_addressed_json,
    _write_content_addressed_parquet,
    read_outcome_partition,
)

MODELED_ROUTE = "Blue"
AUDITED_ROUTES = ("Blue", "Orange", "Red")
SELECTION_LIMIT = 300
SELECTION_SEED = b"arrive90-travel-time-v1-anchor-sample"
BENCHMARK_SEED = b"arrive90-travel-time-v1-benchmark-sample"
BENCHMARK_SAMPLE_MAX = 25_000
WEIGHT_TOLERANCE = 1e-9
QUALIFICATION_PROBES = (
    "CONTROL",
    "FUTURE_OBSERVATION",
    "FINAL_EPISODE_LENGTH",
    "FUTURE_SCHEDULE",
    "POST_OUTCOME_AGGREGATE",
    "SPLIT_LEAKAGE",
)


def _schema_field(name: str, value_type: str) -> pa.Field:
    if value_type == "categorical":
        return pa.field(name, pa.string(), nullable=True)
    if value_type == "boolean":
        return pa.field(name, pa.bool_(), nullable=False)
    if value_type == "integer":
        return pa.field(name, pa.int64(), nullable=False)
    if value_type == "float":
        return pa.field(name, pa.float64(), nullable=False)
    if value_type == "float_or_null":
        return pa.field(name, pa.float64(), nullable=True)
    raise ValueError(f"unsupported feature registry type: {value_type}")


SELECTED_SCHEMA = pa.schema(
    [
        *CANDIDATE_SCHEMA,
        pa.field("selection_digest", pa.string(), nullable=False),
        pa.field("inclusion_probability", pa.float64(), nullable=False),
        pa.field("analysis_weight", pa.float64(), nullable=False),
    ]
)

FEATURE_SCHEMA = pa.schema(
    [
        pa.field("example_id", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("anchor_observation_id", pa.string(), nullable=False),
        pa.field("service_date", pa.date32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("base_weight", pa.float64(), nullable=False),
        pa.field("inclusion_probability", pa.float64(), nullable=False),
        pa.field("analysis_weight", pa.float64(), nullable=False),
        *(
            _schema_field(name, TRAVEL_TIME_V1_REGISTRY.specs[name].value_type)
            for name in TRAVEL_TIME_V1_REGISTRY.specs
        ),
    ]
)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Outcome-safe retained-scope decision and its complete measurements."""

    accepted: bool
    checks: dict[str, bool]
    report: dict[str, object]


@dataclass(frozen=True, slots=True)
class PopulationBuildResult:
    """Immutable selected population and deterministic feature artifacts."""

    manifest_path: Path
    manifest_sha256: str
    selected_anchor_count: int
    selected_example_count: int
    benchmark_report_path: Path
    runtime_report_path: Path


def _required_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise YearDatasetError(f"{field} must be an object")
    return value


def _required_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise YearDatasetError(f"{field} must be a list")
    return value


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise YearDatasetError(f"{field} must be an integer")
    return value


def _required_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise YearDatasetError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise YearDatasetError(f"{field} must be finite")
    return number


def validate_dataset_row_contract(
    *,
    feature_cutoff_utc: datetime,
    schedule_published_at_utc: datetime,
    service_date: date,
    split: str,
    source_observation_times: Sequence[datetime],
    field_names: Iterable[str],
) -> None:
    """Fail closed on availability, split, and feature-schema leakage."""

    require_utc(feature_cutoff_utc, "feature_cutoff_utc")
    require_utc(schedule_published_at_utc, "schedule_published_at_utc")
    if schedule_published_at_utc > feature_cutoff_utc:
        raise YearDatasetError("future schedule publication exceeds the feature cutoff")
    for observed_at in source_observation_times:
        require_utc(observed_at, "source_observation_time")
        if observed_at > feature_cutoff_utc:
            raise YearDatasetError("future observation exceeds the feature cutoff")
    expected_split = chronological_split(service_date).value
    if split != expected_split:
        raise YearDatasetError("split leakage conflicts with the frozen service-date boundary")
    expected_fields = set(FEATURE_SCHEMA.names)
    observed_fields = set(field_names)
    if observed_fields != expected_fields:
        missing = sorted(expected_fields - observed_fields)
        unknown = sorted(observed_fields - expected_fields)
        raise YearDatasetError(
            f"feature population schema mismatch: missing={missing}, unknown={unknown}"
        )


def run_dataset_contract_probe(name: str) -> dict[str, object]:
    """Exercise one seeded builder defect through the production row validator."""

    if name not in QUALIFICATION_PROBES:
        raise YearDatasetError(f"unknown dataset qualification probe: {name}")
    cutoff = datetime(2024, 7, 1, 12, tzinfo=UTC)
    publication = cutoff - timedelta(days=1)
    source_times = [cutoff - timedelta(seconds=30), cutoff]
    fields = list(FEATURE_SCHEMA.names)
    split = DatasetSplit.TRAINING.value
    if name == "FUTURE_OBSERVATION":
        source_times.append(cutoff + timedelta(microseconds=1))
    elif name == "FINAL_EPISODE_LENGTH":
        fields.append("final_episode_length")
    elif name == "FUTURE_SCHEDULE":
        publication = cutoff + timedelta(microseconds=1)
    elif name == "POST_OUTCOME_AGGREGATE":
        fields.append("post_outcome_average_seconds")
    elif name == "SPLIT_LEAKAGE":
        split = DatasetSplit.FINAL_TEST.value
    validate_dataset_row_contract(
        feature_cutoff_utc=cutoff,
        schedule_published_at_utc=publication,
        service_date=cutoff.date(),
        split=split,
        source_observation_times=source_times,
        field_names=fields,
    )
    return {"probe": name, "state": "PASSED"}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _active_unsampled_manifest(dataset_root: Path) -> tuple[Path, dict[str, Any], str]:
    pointer_path = dataset_root / "manifests/active-unsampled.json"
    if pointer_path.is_file():
        pointer = _load_json(pointer_path)
        if pointer.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
            raise YearDatasetError("active unsampled pointer has the wrong acceptance version")
        root = dataset_root.resolve()
        path = (dataset_root / str(pointer.get("path", ""))).resolve()
        if not path.is_relative_to(root):
            raise YearDatasetError("active unsampled pointer escapes the dataset root")
        digest = str(pointer.get("sha256", ""))
        if not path.is_file() or sha256_file(path) != digest:
            raise YearDatasetError("active unsampled pointer failed content verification")
        manifest = _load_json(path)
        if manifest.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
            raise YearDatasetError("active unsampled manifest has the wrong acceptance version")
        if path.stem != f"unsampled-audit-manifest-{digest}":
            raise YearDatasetError("unsampled audit manifest filename does not match its hash")
        days = _required_list(manifest.get("daily_partitions"), "daily_partitions")
        if len(days) != 366:
            raise YearDatasetError("unsampled audit must contain all 366 service dates")
        return path, manifest, digest
    active: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((dataset_root / "manifests").glob("unsampled-audit-manifest-*.json")):
        manifest = _load_json(path)
        if manifest.get("acceptance_version") == DEFAULT_ACCEPTANCE_VERSION:
            active.append((path, manifest))
    if len(active) != 1:
        raise YearDatasetError("exactly one active unsampled audit manifest is required")
    path, manifest = active[0]
    digest = sha256_file(path)
    if path.stem != f"unsampled-audit-manifest-{digest}":
        raise YearDatasetError("unsampled audit manifest filename does not match its content hash")
    days = _required_list(manifest.get("daily_partitions"), "daily_partitions")
    if len(days) != 366:
        raise YearDatasetError("unsampled audit must contain all 366 service dates")
    return path, manifest, digest


def evaluate_blue_retention(manifest: Mapping[str, object]) -> RetentionResult:
    """Evaluate the frozen Blue gate from aggregate audit projections only."""

    days = _required_list(manifest.get("daily_partitions"), "daily_partitions")
    if len(days) != 366:
        raise YearDatasetError("retention requires exactly 366 daily audit projections")
    schedule: dict[int, Counter[str]] = defaultdict(Counter)
    cells: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    split_right_censored: Counter[str] = Counter()
    split_dates: dict[str, set[str]] = defaultdict(set)
    blue_episode_count = 0
    seen_dates: set[str] = set()
    all_routes: set[str] = set()
    for raw_day in days:
        day = _required_mapping(raw_day, "daily partition")
        service_date = str(day.get("service_date", ""))
        split = str(day.get("split", ""))
        if not service_date or service_date in seen_dates:
            raise YearDatasetError("daily audit service dates must be nonempty and unique")
        seen_dates.add(service_date)
        audit = _required_mapping(day.get("audit_projection"), "audit_projection")
        forbidden = {"lower_bound_seconds", "upper_bound_seconds", "duration_seconds"}
        if forbidden.intersection(audit):
            raise YearDatasetError("retention audit projection exposes forbidden duration values")
        episode_by_route = _required_mapping(
            audit.get("episode_count_by_route"), "episode_count_by_route"
        )
        blue_episode_count += _required_int(episode_by_route.get(MODELED_ROUTE, 0), "episodes")
        for raw_schedule in _required_list(audit.get("schedule_cells"), "schedule_cells"):
            schedule_cell = _required_mapping(raw_schedule, "schedule cell")
            route = str(schedule_cell.get("route_id", ""))
            all_routes.add(route)
            if route != MODELED_ROUTE:
                continue
            direction = _required_int(schedule_cell.get("direction_id"), "direction_id")
            counts = _required_mapping(schedule_cell.get("counts"), "schedule counts")
            for key, value in counts.items():
                schedule[direction][str(key)] += _required_int(value, f"schedule count {key}")
        day_has_blue = False
        for raw_cell in _required_list(audit.get("retention_cells"), "retention_cells"):
            cell = _required_mapping(raw_cell, "retention cell")
            route = str(cell.get("route_id", ""))
            all_routes.add(route)
            if route != MODELED_ROUTE:
                continue
            day_has_blue = True
            direction = _required_int(cell.get("direction_id"), "direction_id")
            peak = str(cell.get("peak_period", ""))
            cell_key = (direction, peak)
            for name in (
                "total_example_count",
                "likelihood_example_count",
                "likelihood_distinct_anchor_count",
                "finite_interval_count",
                "finite_width_pass_count",
                "right_censored_count",
            ):
                cells[cell_key][name] += _required_int(cell.get(name), name)
            split_right_censored[split] += _required_int(
                cell.get("right_censored_count"), "right_censored_count"
            )
        if day_has_blue:
            split_dates[split].add(service_date)

    if all_routes != set(AUDITED_ROUTES):
        raise YearDatasetError("retention audit does not cover the three frozen audited routes")
    expected_cells = {(direction, peak) for direction in (0, 1) for peak in ("PEAK", "OFF_PEAK")}
    if set(cells) != expected_cells:
        raise YearDatasetError("Blue retention audit is missing a direction and peak cell")

    schedule_overall = sum((counter for counter in schedule.values()), Counter())
    exact_overall = schedule_overall["scheduled_reason:EXACT"]
    scheduled_overall = schedule_overall["scheduled_episode_count"]
    all_cell_counts = sum((counter for counter in cells.values()), Counter())
    support_overall = _ratio(
        all_cell_counts["likelihood_example_count"], all_cell_counts["total_example_count"]
    )
    width_overall = _ratio(
        all_cell_counts["finite_width_pass_count"], all_cell_counts["finite_interval_count"]
    )
    schedule_rates = {
        str(direction): _ratio(
            counter["scheduled_reason:EXACT"], counter["scheduled_episode_count"]
        )
        for direction, counter in sorted(schedule.items())
    }
    support_rates = {
        f"{direction}|{peak}": _ratio(
            counter["likelihood_example_count"], counter["total_example_count"]
        )
        for (direction, peak), counter in sorted(cells.items())
    }
    width_rates = {
        f"{direction}|{peak}": _ratio(
            counter["finite_width_pass_count"], counter["finite_interval_count"]
        )
        for (direction, peak), counter in sorted(cells.items())
    }
    checks = {
        "audited_routes_complete": all_routes == set(AUDITED_ROUTES),
        "blue_exact_schedule_match_overall": _ratio(exact_overall, scheduled_overall) >= 0.99,
        "blue_exact_schedule_match_per_direction": all(
            rate >= 0.99 for rate in schedule_rates.values()
        )
        and len(schedule_rates) == 2,
        "blue_likelihood_support_overall": support_overall >= 0.75,
        "blue_likelihood_support_per_direction_peak": all(
            rate >= 0.70 for rate in support_rates.values()
        ),
        "blue_interval_width_coverage_overall": width_overall >= 0.90,
        "blue_interval_width_coverage_per_direction_peak": all(
            rate >= 0.80 for rate in width_rates.values()
        ),
        "blue_episode_count": blue_episode_count >= 1_000,
        "blue_cell_likelihood_examples": all(
            counter["likelihood_example_count"] >= 500 for counter in cells.values()
        ),
        "blue_cell_distinct_anchors": all(
            counter["likelihood_distinct_anchor_count"] >= 250 for counter in cells.values()
        ),
        "nontraining_split_service_dates": all(
            len(split_dates[split]) >= 25
            for split in (
                DatasetSplit.MODEL_VALIDATION.value,
                DatasetSplit.CALIBRATION.value,
                DatasetSplit.FINAL_TEST.value,
            )
        ),
        "nontraining_split_right_censored_examples": all(
            split_right_censored[split] >= 100
            for split in (
                DatasetSplit.MODEL_VALIDATION.value,
                DatasetSplit.CALIBRATION.value,
                DatasetSplit.FINAL_TEST.value,
            )
        ),
    }
    report: dict[str, object] = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "audited_routes": list(AUDITED_ROUTES),
        "modeled_routes": [MODELED_ROUTE],
        "rejected_routes": [route for route in AUDITED_ROUTES if route != MODELED_ROUTE],
        "checks": checks,
        "blue_episode_count": blue_episode_count,
        "blue_exact_schedule_match_overall": _ratio(exact_overall, scheduled_overall),
        "blue_exact_schedule_match_per_direction": schedule_rates,
        "blue_likelihood_support_overall": support_overall,
        "blue_likelihood_support_per_direction_peak": support_rates,
        "blue_interval_width_coverage_overall": width_overall,
        "blue_interval_width_coverage_per_direction_peak": width_rates,
        "blue_cell_counts": {
            f"{direction}|{peak}": dict(sorted(counter.items()))
            for (direction, peak), counter in sorted(cells.items())
        },
        "nontraining_service_date_counts": {
            split: len(values) for split, values in sorted(split_dates.items())
        },
        "nontraining_right_censored_counts": dict(sorted(split_right_censored.items())),
        "projection_contract": "aggregate support only; no outcome duration values",
    }
    return RetentionResult(all(checks.values()), checks, report)


def _selection_digest(anchor_id: str) -> str:
    return hmac.new(SELECTION_SEED, anchor_id.encode(), hashlib.sha256).hexdigest()


def _verify_file(root: Path, entry: Mapping[str, object], label: str) -> Path:
    path = root / str(entry.get("path", ""))
    expected = str(entry.get("sha256", ""))
    if not path.is_file() or sha256_file(path) != expected:
        raise YearDatasetError(f"{label} failed content verification: {path}")
    return path


def _selected_rows(table: pa.Table) -> tuple[list[dict[str, object]], dict[str, object]]:
    if table.schema != CANDIDATE_SCHEMA:
        table = table.cast(CANDIDATE_SCHEMA)
    table = table.filter(pc.equal(table["route_id"], MODELED_ROUTE))
    raw_rows = table.to_pylist()
    by_anchor: dict[str, list[dict[str, object]]] = defaultdict(list)
    for raw in raw_rows:
        row = {str(key): value for key, value in raw.items()}
        by_anchor[str(row["anchor_observation_id"])].append(row)
    strata: dict[int, list[str]] = defaultdict(list)
    for anchor_id, rows in by_anchor.items():
        directions = {_required_int(row.get("direction_id"), "direction_id") for row in rows}
        if len(directions) != 1:
            raise YearDatasetError("one anchor cannot cross a direction stratum")
        base_sum = math.fsum(_required_float(row.get("base_weight"), "base_weight") for row in rows)
        if not math.isclose(base_sum, 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise YearDatasetError("base weights must sum to one per unsampled anchor")
        strata[next(iter(directions))].append(anchor_id)
    selected: list[dict[str, object]] = []
    stratum_report: dict[str, object] = {}
    for direction, anchors in sorted(strata.items()):
        ordered = sorted(anchors, key=lambda item: (_selection_digest(item), item.encode()))
        chosen = frozenset(ordered[:SELECTION_LIMIT])
        probability = min(1.0, SELECTION_LIMIT / len(anchors))
        for anchor_id in chosen:
            digest = _selection_digest(anchor_id)
            for row in by_anchor[anchor_id]:
                copied = dict(row)
                copied["selection_digest"] = digest
                copied["inclusion_probability"] = probability
                copied["analysis_weight"] = (
                    _required_float(row.get("base_weight"), "base_weight") / probability
                )
                selected.append(copied)
        stratum_report[str(direction)] = {
            "anchor_count": len(anchors),
            "inclusion_probability": probability,
            "selected_anchor_count": len(chosen),
        }
    selected.sort(
        key=lambda row: (
            str(row["feature_cutoff_utc"]),
            str(row["anchor_observation_id"]).encode(),
            _required_int(row.get("destination_offset"), "destination_offset"),
            str(row["example_id"]).encode(),
        )
    )
    return selected, stratum_report


def _blue_observations(
    service_date: date,
    *,
    partition_index: Mapping[tuple[str, date], Mapping[str, object]],
    normalized_root: Path,
) -> tuple[VehicleObservation, ...]:
    entry = partition_index[(MODELED_ROUTE, service_date)]
    path = _verify_file(normalized_root, entry, "normalized Blue partition")
    observations = read_normalized_partition(path)
    if any(observation.route_id != MODELED_ROUTE for observation in observations):
        raise YearDatasetError("modeled observation partition contains a rejected route")
    return observations


def _feature_rows_for_day(
    selected_rows: Sequence[Mapping[str, object]],
    observations: tuple[VehicleObservation, ...],
    *,
    schedule_database: Path,
    schedule_database_sha256: str,
) -> list[dict[str, object]]:
    observations_by_id = {item.observation_id: item for item in observations}
    episodes = build_trip_episodes(observations)
    schedule = match_episodes_to_schedule(
        schedule_database,
        expanded_database_sha256=schedule_database_sha256,
        episodes=episodes.episodes,
        observations_by_id=observations_by_id,
    )
    matches = {match.episode.episode_id: match for match in schedule.matches}
    views: dict[str, ObservationCutoffView] = {}
    output: list[dict[str, object]] = []
    for candidate in selected_rows:
        episode_id = str(candidate["episode_id"])
        anchor_id = str(candidate["anchor_observation_id"])
        match = matches.get(episode_id)
        if match is None or match.reason is not ScheduleMatchReason.EXACT:
            raise YearDatasetError("selected candidate no longer has an exact schedule match")
        anchor = observations_by_id.get(anchor_id)
        if anchor is None:
            raise YearDatasetError("selected candidate anchor is absent from normalized inputs")
        view = views.get(anchor_id)
        if view is None:
            view = ObservationCutoffView.from_episode(
                match.episode,
                observations_by_id,
                cutoff_utc=anchor.observation_utc,
            )
            views[anchor_id] = view
        feature = build_travel_time_feature_row(
            match,
            view,
            anchor_observation_id=anchor_id,
            destination_stop_id=str(candidate["destination_stop_id"]),
            destination_stop_sequence=_required_int(
                candidate.get("destination_stop_sequence"), "destination_stop_sequence"
            ),
            destination_offset=_required_int(
                candidate.get("destination_offset"), "destination_offset"
            ),
            scheduled_remaining_seconds=_required_int(
                candidate.get("scheduled_remaining_seconds"), "scheduled_remaining_seconds"
            ),
        )
        values = dict(feature.values)
        for name in CATEGORICAL_FEATURES:
            if values[name] is not None:
                values[name] = str(values[name])
        row: dict[str, object] = {
            "example_id": str(candidate["example_id"]),
            "episode_id": episode_id,
            "anchor_observation_id": anchor_id,
            "service_date": candidate["service_date"],
            "split": str(candidate["split"]),
            "base_weight": _required_float(candidate.get("base_weight"), "base_weight"),
            "inclusion_probability": _required_float(
                candidate.get("inclusion_probability"), "inclusion_probability"
            ),
            "analysis_weight": _required_float(candidate.get("analysis_weight"), "analysis_weight"),
        }
        row.update(values)
        candidate_date = candidate.get("service_date")
        if not isinstance(candidate_date, date):
            raise YearDatasetError("selected candidate service date must be a date")
        scheduled_trip = match.scheduled_trip
        if scheduled_trip is None:
            raise YearDatasetError("exact schedule match must include a scheduled trip")
        validate_dataset_row_contract(
            feature_cutoff_utc=feature.feature_cutoff_utc,
            schedule_published_at_utc=scheduled_trip.published_at_utc,
            service_date=candidate_date,
            split=str(candidate["split"]),
            source_observation_times=[
                observations_by_id[identifier].observation_utc
                for identifier in feature.source_observation_ids
            ],
            field_names=row,
        )
        output.append(row)
    output.sort(key=lambda row: str(row["example_id"]).encode())
    if len({str(row["example_id"]) for row in output}) != len(output):
        raise YearDatasetError("selected feature example identifiers must be unique")
    return output


def _feature_input(row: Mapping[str, object]) -> FeatureTransformInput:
    expected = set(FEATURE_SCHEMA.names)
    observed = set(row)
    if observed != expected:
        raise ValueError(
            f"feature population schema mismatch: missing={sorted(expected - observed)}, "
            f"unknown={sorted(observed - expected)}"
        )
    return FeatureTransformInput(
        row_id=str(row["example_id"]),
        values={name: cast(FeatureValue, row[name]) for name in TRAVEL_TIME_V1_REGISTRY.specs},
    )


def _fit_partitioned_transform(
    feature_entries: Sequence[Mapping[str, object]], dataset_root: Path
) -> FittedFeatureTransform:
    vocabularies: dict[str, set[str]] = {name: set() for name in CATEGORICAL_FEATURES}
    training_hash = hashlib.sha256()
    training_count = 0
    for entry in feature_entries:
        if entry.get("split") != DatasetSplit.TRAINING.value:
            continue
        path = _verify_file(dataset_root, entry, "training feature partition")
        for raw in pq.read_table(path, schema=FEATURE_SCHEMA).to_pylist():
            row = {str(key): value for key, value in raw.items()}
            feature = _feature_input(row)
            training_hash.update(
                _canonical_json(
                    {
                        "row_id": feature.row_id,
                        "values": {
                            name: feature.values[name] for name in TRAVEL_TIME_V1_REGISTRY.specs
                        },
                    }
                )
            )
            training_hash.update(b"\n")
            training_count += 1
            for name in CATEGORICAL_FEATURES:
                value = feature.values[name]
                if value is None:
                    continue
                text = str(value)
                if text in {MISSING_TOKEN, UNKNOWN_TOKEN}:
                    raise YearDatasetError("raw category equals a reserved transform token")
                if not text:
                    raise YearDatasetError("raw categorical values cannot be empty")
                vocabularies[name].add(text)
    if training_count == 0:
        raise YearDatasetError("selected training feature population is empty")
    frozen_vocabularies = tuple(
        (name, (MISSING_TOKEN, UNKNOWN_TOKEN, *sorted(vocabularies[name], key=str.encode)))
        for name in CATEGORICAL_FEATURES
    )
    column_names = list(NUMERIC_FEATURES)
    for name, vocabulary in frozen_vocabularies:
        column_names.extend(f"{name}={value}" for value in vocabulary)
    output_hash = hashlib.sha256(_canonical_json(column_names)).hexdigest()
    provisional = FittedFeatureTransform(
        training_row_sha256=training_hash.hexdigest(),
        vocabularies=frozen_vocabularies,
        column_names=tuple(column_names),
        output_schema_sha256=output_hash,
        csr_index_dtype="PENDING",
    )
    first_training = next(
        entry for entry in feature_entries if entry.get("split") == DatasetSplit.TRAINING.value
    )
    first_path = _verify_file(dataset_root, first_training, "training feature partition")
    first_row = pq.read_table(first_path, schema=FEATURE_SCHEMA).slice(0, 1).to_pylist()
    if not first_row:
        raise YearDatasetError("first selected training feature partition is empty")
    index_dtype = str(provisional.transform((_feature_input(first_row[0]),)).indices.dtype)
    return FittedFeatureTransform(
        training_row_sha256=provisional.training_row_sha256,
        vocabularies=provisional.vocabularies,
        column_names=provisional.column_names,
        output_schema_sha256=provisional.output_schema_sha256,
        csr_index_dtype=index_dtype,
    )


def _physical_memory_bytes() -> int:
    page_size = os.sysconf("SC_PAGE_SIZE")
    page_count = os.sysconf("SC_PHYS_PAGES")
    return int(page_size) * int(page_count)


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1_024)


def _benchmark_key(example_id: str) -> int:
    return int.from_bytes(hmac.digest(BENCHMARK_SEED, example_id.encode(), "sha256"), "big")


def _benchmark_sample(
    feature_entries: Sequence[Mapping[str, object]],
    day_by_date: Mapping[str, Mapping[str, object]],
    dataset_root: Path,
) -> tuple[list[FeatureTransformInput], np.ndarray, np.ndarray, np.ndarray]:
    heap: list[tuple[int, str, FeatureTransformInput, float, float, float]] = []
    eligible = {
        DownstreamOutcomeState.INTERVAL_RESOLVED.value,
        DownstreamOutcomeState.LEFT_CENSORED.value,
        DownstreamOutcomeState.RIGHT_CENSORED.value,
    }
    for entry in feature_entries:
        if entry.get("split") != DatasetSplit.TRAINING.value:
            continue
        service_date = str(entry["service_date"])
        day = day_by_date[service_date]
        outcomes_entry = _required_mapping(day.get("outcomes"), "outcome entry")
        if outcomes_entry.get("sealed") is not False:
            raise YearDatasetError("training outcomes cannot be sealed")
        outcome_path = _verify_file(dataset_root, outcomes_entry, "training outcome partition")
        outcome_table = read_outcome_partition(
            outcome_path, split=DatasetSplit.TRAINING, requesting_milestone=2
        )
        outcomes = {
            str(row["example_id"]): row
            for row in outcome_table.to_pylist()
            if row["outcome_state"] in eligible
        }
        feature_path = _verify_file(dataset_root, entry, "training feature partition")
        for raw in pq.read_table(feature_path, schema=FEATURE_SCHEMA).to_pylist():
            example_id = str(raw["example_id"])
            outcome = outcomes.get(example_id)
            if outcome is None:
                continue
            lower = float(outcome["lower_bound_seconds"])
            upper = float(outcome["upper_bound_seconds"])
            weight = float(raw["analysis_weight"])
            if lower < 0 or upper <= 0 or upper < lower or weight <= 0:
                raise YearDatasetError("benchmark labels or weights violate the AFT contract")
            item = (
                -_benchmark_key(example_id),
                example_id,
                _feature_input(raw),
                lower,
                upper,
                weight,
            )
            if len(heap) < BENCHMARK_SAMPLE_MAX:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
    selected = sorted(heap, key=lambda item: (-item[0], item[1].encode()))
    if len(selected) < 1_000:
        raise YearDatasetError("benchmark requires at least 1,000 eligible training examples")
    return (
        [item[2] for item in selected],
        np.asarray([item[3] for item in selected], dtype=np.float32),
        np.asarray([item[4] for item in selected], dtype=np.float32),
        np.asarray([item[5] for item in selected], dtype=np.float32),
    )


def _run_benchmark(
    transform: FittedFeatureTransform,
    feature_entries: Sequence[Mapping[str, object]],
    day_by_date: Mapping[str, Mapping[str, object]],
    dataset_root: Path,
    *,
    selected_example_count: int,
) -> dict[str, object]:
    rows, lower, upper, weights = _benchmark_sample(feature_entries, day_by_date, dataset_root)
    baseline_rss = _rss_bytes()
    started = time.monotonic()
    matrix = transform.transform(rows)
    matrix_bytes = int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
    dmatrix = xgb.DMatrix(matrix, feature_names=list(transform.column_names), weight=weights)
    dmatrix.set_float_info("label_lower_bound", lower)
    dmatrix.set_float_info("label_upper_bound", upper)
    xgb.train(
        {
            "aft_loss_distribution": "normal",
            "aft_loss_distribution_scale": 1.0,
            "eval_metric": "aft-nloglik",
            "max_depth": 2,
            "nthread": 1,
            "objective": "survival:aft",
            "seed": 90,
            "subsample": 1.0,
            "tree_method": "hist",
        },
        dmatrix,
        num_boost_round=2,
    )
    elapsed = time.monotonic() - started
    peak_rss = _rss_bytes()
    sample_count = len(rows)
    scale = selected_example_count / sample_count
    incremental_peak = max(peak_rss - baseline_rss, matrix_bytes * 2)
    projected_peak = math.ceil(baseline_rss + incremental_peak * scale)
    projected_runtime = elapsed * scale
    physical_memory = _physical_memory_bytes()
    disk = shutil.disk_usage(dataset_root)
    return {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "elapsed_seconds": elapsed,
        "extrapolation_method": (
            "linear selected-row scaling of measured incremental process peak and elapsed time; "
            "fixed interpreter baseline retained once"
        ),
        "hardware": {
            "logical_cpu_count": os.cpu_count(),
            "machine": platform.machine(),
            "physical_memory_bytes": physical_memory,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "scipy": scipy.__version__,
            "xgboost": xgb.__version__,
        },
        "matrix_column_count": matrix.shape[1],
        "matrix_nnz": int(matrix.nnz),
        "measured_matrix_bytes": matrix_bytes,
        "measured_process_baseline_bytes": baseline_rss,
        "measured_process_peak_bytes": peak_rss,
        "measured_sample_size": sample_count,
        "measured_temporary_bytes": 0,
        "projected_peak_memory_bytes": projected_peak,
        "projected_runtime_seconds": projected_runtime,
        "projected_temporary_bytes": 0,
        "qualification_free_disk_bytes": disk.free,
        "selected_population_size": selected_example_count,
        "temporary_storage_method": "in-memory CSR and DMatrix; no temporary training file",
        "within_memory_budget": projected_peak <= int(physical_memory * 0.70),
        "within_temporary_storage_budget": int(disk.free * 0.50) >= 0,
    }


def _write_runtime_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_model_population(
    *,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    schedule_database: Path = DEFAULT_SCHEDULE_DATABASE,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> PopulationBuildResult:
    """Build the complete retained Blue population from an active unsampled audit."""

    started = time.monotonic()
    unsampled_path, unsampled, unsampled_sha256 = _active_unsampled_manifest(dataset_root)
    _, normalized, normalized_sha256 = _normalized_manifest(normalized_root)
    if unsampled.get("normalized_manifest_sha256") != normalized_sha256:
        raise YearDatasetError("unsampled and normalized manifests do not share the same input")
    schedule_hash = _schedule_database_hash(normalized, normalized_root)
    if not schedule_database.is_file() or sha256_file(schedule_database) != schedule_hash:
        raise YearDatasetError("expanded schedule database failed content verification")
    retention = evaluate_blue_retention(unsampled)
    if not retention.accepted:
        failing = sorted(name for name, passed in retention.checks.items() if not passed)
        raise YearDatasetError(f"Blue retention gate failed: {', '.join(failing)}")
    retention_path, retention_sha256 = _write_content_addressed_json(
        dataset_root / "audit", "blue-retention", retention.report
    )

    partition_index = _partition_index(normalized)
    days = _required_list(unsampled.get("daily_partitions"), "daily_partitions")
    selection_entries: list[dict[str, object]] = []
    feature_entries: list[dict[str, object]] = []
    selected_anchor_count = 0
    selected_example_count = 0
    day_by_date: dict[str, Mapping[str, object]] = {}
    for raw_day in days:
        day = _required_mapping(raw_day, "daily partition")
        service_date_text = str(day["service_date"])
        day_by_date[service_date_text] = day
        service_date = date.fromisoformat(service_date_text)
        candidate_entry = _required_mapping(day.get("candidate_index"), "candidate index")
        candidate_path = _verify_file(dataset_root, candidate_entry, "candidate partition")
        candidates = pq.read_table(candidate_path, schema=CANDIDATE_SCHEMA)
        selected_rows, strata = _selected_rows(candidates)
        selected_path, selected_sha256, selected_bytes = _write_content_addressed_parquet(
            dataset_root / "selected/candidates" / f"service_date={service_date_text}",
            "selected-candidates",
            tuple(selected_rows),
            SELECTED_SCHEMA,
        )
        anchor_count = sum(
            _required_int(value["selected_anchor_count"], "selected anchor count")
            for value in strata.values()
            if isinstance(value, dict)
        )
        selection_entry = {
            "bytes": selected_bytes,
            "path": selected_path.relative_to(dataset_root).as_posix(),
            "row_count": len(selected_rows),
            "service_date": service_date_text,
            "sha256": selected_sha256,
            "split": str(day["split"]),
            "strata": strata,
        }
        selection_entries.append(selection_entry)
        selected_anchor_count += anchor_count
        selected_example_count += len(selected_rows)

        observations = _blue_observations(
            service_date,
            partition_index=partition_index,
            normalized_root=normalized_root,
        )
        feature_rows = _feature_rows_for_day(
            selected_rows,
            observations,
            schedule_database=schedule_database,
            schedule_database_sha256=schedule_hash,
        )
        feature_path, feature_sha256, feature_bytes = _write_content_addressed_parquet(
            dataset_root / "selected/features" / f"service_date={service_date_text}",
            "features",
            tuple(feature_rows),
            FEATURE_SCHEMA,
        )
        feature_entries.append(
            {
                "bytes": feature_bytes,
                "path": feature_path.relative_to(dataset_root).as_posix(),
                "row_count": len(feature_rows),
                "service_date": service_date_text,
                "sha256": feature_sha256,
                "split": str(day["split"]),
            }
        )

    transform = _fit_partitioned_transform(feature_entries, dataset_root)
    transform_payload = {
        **transform.manifest,
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "training_row_hash_algorithm": (
            "sha256 canonical-json row plus newline in service-date order"
        ),
    }
    transform_path, transform_sha256 = _write_content_addressed_json(
        dataset_root / "transforms", "travel-time-transform", transform_payload
    )
    benchmark = _run_benchmark(
        transform,
        feature_entries,
        day_by_date,
        dataset_root,
        selected_example_count=selected_example_count,
    )
    benchmark_path = runtime_root / "dmatrix-benchmark.json"
    _write_runtime_json(benchmark_path, benchmark)
    if not benchmark["within_memory_budget"] or not benchmark["within_temporary_storage_budget"]:
        raise YearDatasetError("projected training resources exceed the frozen budget")

    manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "dataset_version": "travel-time-model-population-v1",
        "feature_partitions": feature_entries,
        "invariants": {
            "feature_partitions_contain_no_outcomes": True,
            "final_test_outcomes_remain_sealed": True,
            "modeled_routes": [MODELED_ROUTE],
            "rejected_routes_contribute_nothing": True,
            "selection_uses_identity_only": True,
            "service_date_split_is_exclusive": True,
        },
        "normalized_manifest_sha256": normalized_sha256,
        "retention_report": {
            "path": retention_path.relative_to(dataset_root).as_posix(),
            "sha256": retention_sha256,
        },
        "sampling": {
            "algorithm": "HMAC-SHA-256 ascending anchor identifier digest",
            "anchor_limit_per_service_date_route_direction": SELECTION_LIMIT,
            "seed": SELECTION_SEED.decode(),
        },
        "selection_partitions": selection_entries,
        "summary": {
            "selected_anchor_count": selected_anchor_count,
            "selected_example_count": selected_example_count,
            "service_day_count": len(selection_entries),
        },
        "transform": {
            "path": transform_path.relative_to(dataset_root).as_posix(),
            "sha256": transform_sha256,
        },
        "unsampled_manifest_sha256": unsampled_sha256,
        "year": 2024,
    }
    manifest_path, manifest_sha256 = _write_content_addressed_json(
        dataset_root / "manifests", "model-population-manifest", manifest
    )
    _write_atomic_json(
        dataset_root / "manifests/active-model-population.json",
        {
            "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
            "path": manifest_path.relative_to(dataset_root).as_posix(),
            "sha256": manifest_sha256,
        },
    )
    runtime = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "benchmark_report_path": str(benchmark_path),
        "elapsed_seconds": time.monotonic() - started,
        "manifest_path": manifest_path.relative_to(dataset_root).as_posix(),
        "manifest_sha256": manifest_sha256,
        "maximum_concurrent_service_dates": 1,
        "selected_anchor_count": selected_anchor_count,
        "selected_example_count": selected_example_count,
        "unsampled_manifest_path": unsampled_path.resolve()
        .relative_to(dataset_root.resolve())
        .as_posix(),
    }
    runtime_path = runtime_root / "model-population-run.json"
    _write_runtime_json(runtime_path, runtime)
    return PopulationBuildResult(
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        selected_anchor_count=selected_anchor_count,
        selected_example_count=selected_example_count,
        benchmark_report_path=benchmark_path,
        runtime_report_path=runtime_path,
    )


def assert_final_test_sealed(
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    *,
    requesting_milestone: int = 2,
) -> None:
    """Exercise the final-test access guard for every sealed partition."""

    _, manifest, _ = _active_unsampled_manifest(dataset_root)
    for raw_day in _required_list(manifest.get("daily_partitions"), "daily_partitions"):
        day = _required_mapping(raw_day, "daily partition")
        if day.get("split") != DatasetSplit.FINAL_TEST.value:
            continue
        outcome = _required_mapping(day.get("outcomes"), "outcome entry")
        path = _verify_file(dataset_root, outcome, "sealed final outcome partition")
        try:
            read_outcome_partition(
                path,
                split=DatasetSplit.FINAL_TEST,
                requesting_milestone=requesting_milestone,
            )
        except FinalTestOutcomeAccessError:
            continue
        raise YearDatasetError("Milestone 2 unexpectedly opened a sealed final-test outcome")
