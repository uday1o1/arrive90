"""File-bounded unsampled full-year dataset audit and sealed outcome construction."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.dataset import (
    DatasetSplit,
    chronological_split,
    destination_class,
    peak_period,
)
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.travel_time import (
    DownstreamOutcomeState,
    DownstreamStopExample,
    TripScheduleRelationship,
    VehicleObservation,
)
from arrive90_ingestion.acquisition import sha256_file
from arrive90_ingestion.episodes import EpisodeBuildResult, build_trip_episodes
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    ScheduleMatchReason,
    ScheduleMatchResult,
    match_episodes_to_schedule,
)
from arrive90_ingestion.year_normalization import read_normalized_partition
from arrive90_outcomes.travel_time import TargetBuildResult, build_downstream_examples

YEAR = 2024
DATASET_VERSION = "travel-time-dataset-v1"
RAIL_ROUTES = ("Blue", "Orange", "Red")
DEFAULT_NORMALIZED_ROOT = Path("data/normalized")
DEFAULT_DATASET_ROOT = Path("data/datasets/travel-time-v1")
DEFAULT_SCHEDULE_DATABASE = Path("data/raw/mbta-gtfs/2024/GTFS_ARCHIVE.db")
DEFAULT_RUNTIME_ROOT = Path("artifacts/runtime/milestone-2")

CANDIDATE_SCHEMA = pa.schema(
    [
        pa.field("example_id", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("anchor_observation_id", pa.string(), nullable=False),
        pa.field("service_date", pa.date32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("route_id", pa.string(), nullable=False),
        pa.field("direction_id", pa.int8(), nullable=False),
        pa.field("feature_cutoff_utc", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("peak_period", pa.string(), nullable=False),
        pa.field("destination_stop_id", pa.string(), nullable=False),
        pa.field("destination_stop_sequence", pa.int32(), nullable=False),
        pa.field("destination_offset", pa.int8(), nullable=False),
        pa.field("destination_class", pa.string(), nullable=False),
        pa.field("scheduled_remaining_seconds", pa.int32(), nullable=False),
        pa.field("base_weight", pa.float64(), nullable=False),
        pa.field("schedule_version_id", pa.string(), nullable=False),
        pa.field("route_pattern_id", pa.string(), nullable=False),
    ]
)

OUTCOME_SCHEMA = pa.schema(
    [
        pa.field("example_id", pa.string(), nullable=False),
        pa.field("outcome_state", pa.string(), nullable=False),
        pa.field("lower_evidence_observation_id", pa.string()),
        pa.field("upper_evidence_observation_id", pa.string()),
        pa.field("lower_bound_seconds", pa.float64()),
        pa.field("upper_bound_seconds", pa.float64()),
    ]
)


class YearDatasetError(ValueError):
    """Raised when immutable inputs cannot satisfy the full-year dataset contract."""


class FinalTestOutcomeAccessError(PermissionError):
    """Raised when a pre-Milestone 4 workflow attempts to read sealed outcomes."""


@dataclass(frozen=True, slots=True)
class DailyRecords:
    """Outcome-free candidate rows, outcomes, and aggregate audit facts for one day."""

    candidate_rows: tuple[dict[str, object], ...]
    outcome_rows: tuple[dict[str, object], ...]
    audit: dict[str, object]


@dataclass(frozen=True, slots=True)
class UnsampledAuditResult:
    """Content-addressed full-year audit output from the public source dataset."""

    manifest_path: Path
    manifest_sha256: str
    candidate_example_count: int
    outcome_example_count: int
    episode_count: int
    runtime_report_path: Path


def _load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise YearDatasetError(f"{path} must contain a JSON object")
    return loaded


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()


def _write_content_addressed_json(root: Path, stem: str, payload: object) -> tuple[Path, str]:
    body = _canonical_json(payload)
    digest = hashlib.sha256(body).hexdigest()
    path = root / f"{stem}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise YearDatasetError(f"content-addressed JSON path has different bytes: {path}")
    if not path.exists():
        path.write_bytes(body)
    return path, digest


def _write_atomic_json(path: Path, payload: object) -> None:
    body = _canonical_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _write_content_addressed_parquet(
    root: Path,
    stem: str,
    rows: tuple[dict[str, object], ...],
    schema: pa.Schema,
) -> tuple[Path, str, int]:
    root.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(list(rows), schema=schema)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{stem}-", suffix=".parquet", dir=root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            compression_level=9,
            data_page_version="2.0",
            row_group_size=50_000,
            use_dictionary=False,
            version="2.6",
            write_statistics=True,
        )
        digest = sha256_file(temporary)
        path = root / f"{stem}-{digest}.parquet"
        if path.exists():
            if path.read_bytes() != temporary.read_bytes():
                raise YearDatasetError(f"content-addressed Parquet path differs: {path}")
            temporary.unlink()
        else:
            os.replace(temporary, path)
        return path, digest, path.stat().st_size
    finally:
        temporary.unlink(missing_ok=True)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _finite_width(example: DownstreamStopExample) -> float | None:
    if example.lower_bound_seconds is None or example.upper_bound_seconds is None:
        return None
    if not math.isfinite(example.upper_bound_seconds):
        return None
    return example.upper_bound_seconds - example.lower_bound_seconds


def _required_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise YearDatasetError(f"{key} must be an integer")
    return value


def _counter(value: object, field: str) -> Counter[str]:
    if not isinstance(value, dict):
        raise YearDatasetError(f"{field} must be a count mapping")
    counts: Counter[str] = Counter()
    for key, count in value.items():
        if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int):
            raise YearDatasetError(f"{field} must contain string integer pairs")
        counts[key] = count
    return counts


def _candidate_row(
    example: DownstreamStopExample,
    match: EpisodeScheduleMatch,
) -> dict[str, object] | None:
    trip = match.scheduled_trip
    if (
        match.reason is not ScheduleMatchReason.EXACT
        or trip is None
        or example.destination_stop_id is None
        or example.destination_stop_sequence is None
        or example.destination_offset is None
        or example.scheduled_remaining_seconds is None
    ):
        return None
    terminal = example.destination_stop_sequence == trip.stops[-1].stop_sequence
    return {
        "anchor_observation_id": example.anchor_observation_id,
        "base_weight": example.base_weight,
        "destination_class": destination_class(
            example.destination_offset, is_terminal=terminal
        ).value,
        "destination_offset": example.destination_offset,
        "destination_stop_id": example.destination_stop_id,
        "destination_stop_sequence": example.destination_stop_sequence,
        "direction_id": match.episode.direction_id,
        "episode_id": example.episode_id,
        "example_id": example.example_id,
        "feature_cutoff_utc": example.feature_cutoff_utc,
        "peak_period": peak_period(example.feature_cutoff_utc).value,
        "route_id": match.episode.route_id,
        "route_pattern_id": trip.route_pattern_id,
        "schedule_version_id": trip.schedule_version_id,
        "scheduled_remaining_seconds": example.scheduled_remaining_seconds,
        "service_date": example.service_date,
        "split": chronological_split(example.service_date).value,
    }


def _outcome_row(example: DownstreamStopExample) -> dict[str, object]:
    return {
        "example_id": example.example_id,
        "lower_bound_seconds": example.lower_bound_seconds,
        "lower_evidence_observation_id": example.lower_evidence_observation_id,
        "outcome_state": example.outcome_state.value,
        "upper_bound_seconds": example.upper_bound_seconds,
        "upper_evidence_observation_id": example.upper_evidence_observation_id,
    }


def _cell_key(route_id: str, direction_id: int, peak: str) -> str:
    return f"{route_id}|{direction_id}|{peak}"


def build_daily_records(
    episode_result: EpisodeBuildResult,
    schedule_result: ScheduleMatchResult,
    target_result: TargetBuildResult,
    observations_by_id: dict[str, VehicleObservation],
) -> DailyRecords:
    """Build the outcome-safe audit projection and separate outcome rows for one day."""

    matches_by_episode = {match.episode.episode_id: match for match in schedule_result.matches}
    if len(matches_by_episode) != len(schedule_result.matches):
        raise YearDatasetError("schedule matches must contain unique episode identifiers")
    audit_cells: dict[str, dict[str, object]] = {}
    anchor_sets: dict[str, set[str]] = defaultdict(set)
    state_counts: Counter[str] = Counter()
    candidate_rows: list[dict[str, object]] = []
    outcome_rows: list[dict[str, object]] = []
    for example in target_result.examples:
        match = matches_by_episode[example.episode_id]
        anchor = observations_by_id[example.anchor_observation_id]
        peak = peak_period(anchor.observation_utc).value
        cell_key = _cell_key(match.episode.route_id, match.episode.direction_id, peak)
        cell = audit_cells.setdefault(
            cell_key,
            {
                "direction_id": match.episode.direction_id,
                "finite_interval_count": 0,
                "finite_width_pass_count": 0,
                "likelihood_distinct_anchor_count": 0,
                "likelihood_example_count": 0,
                "outcome_state_counts": {},
                "peak_period": peak,
                "route_id": match.episode.route_id,
                "right_censored_count": 0,
                "total_example_count": 0,
            },
        )
        cell["total_example_count"] = _required_int(cell, "total_example_count") + 1
        cell_states = _counter(cell["outcome_state_counts"], "outcome_state_counts")
        cell_states[example.outcome_state.value] += 1
        cell["outcome_state_counts"] = dict(sorted(cell_states.items()))
        state_counts[example.outcome_state.value] += 1
        if example.included_in_likelihood:
            cell["likelihood_example_count"] = _required_int(cell, "likelihood_example_count") + 1
            anchor_sets[cell_key].add(example.anchor_observation_id)
        if example.outcome_state is DownstreamOutcomeState.RIGHT_CENSORED:
            cell["right_censored_count"] = _required_int(cell, "right_censored_count") + 1
        width = _finite_width(example)
        if width is not None:
            cell["finite_interval_count"] = _required_int(cell, "finite_interval_count") + 1
            if width <= 180:
                cell["finite_width_pass_count"] = _required_int(cell, "finite_width_pass_count") + 1
        candidate = _candidate_row(example, match)
        if candidate is not None:
            candidate_rows.append(candidate)
        outcome_rows.append(_outcome_row(example))

    for key, anchors in anchor_sets.items():
        audit_cells[key]["likelihood_distinct_anchor_count"] = len(anchors)

    episode_counts: Counter[str] = Counter()
    schedule_cells: dict[str, Counter[str]] = defaultdict(Counter)
    for match in schedule_result.matches:
        episode_counts[match.episode.route_id] += 1
        key = f"{match.episode.route_id}|{match.episode.direction_id}"
        schedule_cells[key]["episode_count"] += 1
        schedule_cells[key][f"reason:{match.reason.value}"] += 1
        relationships = {
            observations_by_id[identifier].schedule_relationship
            for identifier in match.episode.observation_ids
        }
        if relationships == {TripScheduleRelationship.SCHEDULED}:
            schedule_cells[key]["scheduled_episode_count"] += 1
            schedule_cells[key][f"scheduled_reason:{match.reason.value}"] += 1
        else:
            schedule_cells[key]["non_scheduled_episode_count"] += 1

    service_dates = {match.episode.service_date for match in schedule_result.matches}
    if len(service_dates) != 1:
        raise YearDatasetError("daily records must contain exactly one service date")
    service_date = next(iter(service_dates))
    audit = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "ambiguous_event_group_count": episode_result.ambiguous_event_group_count,
        "anchor_count": target_result.anchor_count,
        "candidate_example_count": len(candidate_rows),
        "episode_count": len(episode_result.episodes),
        "episode_count_by_route": dict(sorted(episode_counts.items())),
        "gap_split_count": episode_result.gap_split_count,
        "matched_anchor_count": target_result.matched_anchor_count,
        "outcome_example_count": len(outcome_rows),
        "outcome_state_counts": dict(sorted(state_counts.items())),
        "raw_timestamp_regression_session_count": (
            episode_result.raw_timestamp_regression_session_count
        ),
        "retention_cells": [audit_cells[key] for key in sorted(audit_cells)],
        "schedule_cells": [
            {
                "counts": dict(sorted(schedule_cells[key].items())),
                "direction_id": int(key.split("|")[1]),
                "route_id": key.split("|")[0],
            }
            for key in sorted(schedule_cells)
        ],
        "service_date": service_date.isoformat(),
        "split": chronological_split(service_date).value,
        "stop_sequence_regression_split_count": (
            episode_result.stop_sequence_regression_split_count
        ),
        "terminal_anchor_count": target_result.terminal_anchor_count,
    }
    candidate_rows.sort(
        key=lambda row: (
            str(row["feature_cutoff_utc"]),
            str(row["episode_id"]),
            _required_int(row, "destination_offset"),
            str(row["example_id"]),
        )
    )
    outcome_rows.sort(key=lambda row: str(row["example_id"]).encode())
    return DailyRecords(tuple(candidate_rows), tuple(outcome_rows), audit)


def read_outcome_partition(
    path: Path,
    *,
    split: DatasetSplit,
    requesting_milestone: int,
) -> pa.Table:
    """Read an outcome partition while enforcing the frozen final-test seal."""

    if split is DatasetSplit.FINAL_TEST and requesting_milestone < 4:
        raise FinalTestOutcomeAccessError(
            "final-test duration bounds remain sealed until Milestone 4"
        )
    return pq.read_table(path, schema=OUTCOME_SCHEMA)


def _normalized_manifest(normalized_root: Path) -> tuple[Path, dict[str, Any], str]:
    candidates = sorted((normalized_root / "manifests" / str(YEAR)).glob("dataset-manifest-*.json"))
    active = [
        (candidate, manifest)
        for candidate in candidates
        if (manifest := _load_json(candidate)).get("acceptance_version")
        == DEFAULT_ACCEPTANCE_VERSION
    ]
    if len(active) != 1:
        raise YearDatasetError("exactly one active complete-year normalized manifest is required")
    path, manifest = active[0]
    digest = sha256_file(path)
    if path.stem != f"dataset-manifest-{digest}":
        raise YearDatasetError("normalized manifest filename does not match its content hash")
    return path, manifest, digest


def _partition_index(manifest: dict[str, Any]) -> dict[tuple[str, date], dict[str, Any]]:
    raw_partitions = manifest.get("partitions")
    if not isinstance(raw_partitions, list):
        raise YearDatasetError("normalized manifest partitions must be a list")
    indexed: dict[tuple[str, date], dict[str, Any]] = {}
    for raw in raw_partitions:
        if not isinstance(raw, dict):
            raise YearDatasetError("normalized partition entries must be objects")
        key = (str(raw["route_id"]), date.fromisoformat(str(raw["service_date"])))
        if key in indexed:
            raise YearDatasetError("normalized route-day partition keys must be unique")
        indexed[key] = raw
    expected = {
        (route, date.fromordinal(ordinal))
        for route in RAIL_ROUTES
        for ordinal in range(date(YEAR, 1, 1).toordinal(), date(YEAR, 12, 31).toordinal() + 1)
    }
    if set(indexed) != expected:
        raise YearDatasetError("normalized route-day partition coverage is incomplete")
    return indexed


def _load_observations(
    service_date: date,
    *,
    indexed: dict[tuple[str, date], dict[str, Any]],
    normalized_root: Path,
) -> tuple[VehicleObservation, ...]:
    observations: list[VehicleObservation] = []
    for route in RAIL_ROUTES:
        partition = indexed[(route, service_date)]
        path = normalized_root / str(partition["path"])
        if not path.is_file() or sha256_file(path) != partition["sha256"]:
            raise YearDatasetError(f"normalized partition failed verification: {path}")
        observations.extend(read_normalized_partition(path))
    if len({observation.observation_id for observation in observations}) != len(observations):
        raise YearDatasetError("daily canonical observation identifiers must be unique")
    return tuple(observations)


def _schedule_database_hash(manifest: dict[str, Any], normalized_root: Path) -> str:
    raw_schedule = manifest.get("schedule_index")
    if not isinstance(raw_schedule, dict):
        raise YearDatasetError("normalized manifest schedule index is invalid")
    index_path = normalized_root / str(raw_schedule["path"])
    if sha256_file(index_path) != raw_schedule.get("sha256"):
        raise YearDatasetError("schedule index failed content verification")
    schedule = _load_json(index_path)
    database_hash = schedule.get("database_sha256")
    if not isinstance(database_hash, str) or len(database_hash) != 64:
        raise YearDatasetError("schedule index database hash is invalid")
    return database_hash


def _build_unsampled_audit_for_dates(
    service_dates: tuple[date, ...],
    *,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    schedule_database: Path = DEFAULT_SCHEDULE_DATABASE,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> UnsampledAuditResult:
    """Build an audit for an already validated ordered set of service dates."""

    if not service_dates or service_dates != tuple(sorted(set(service_dates))):
        raise YearDatasetError("audit service dates must be nonempty, unique, and ordered")
    if any(service_date.year != YEAR for service_date in service_dates):
        raise YearDatasetError("audit service dates must remain inside 2024")

    started = time.monotonic()
    _, normalized_manifest, normalized_manifest_sha256 = _normalized_manifest(normalized_root)
    indexed = _partition_index(normalized_manifest)
    database_hash = _schedule_database_hash(normalized_manifest, normalized_root)
    if not schedule_database.is_file() or sha256_file(schedule_database) != database_hash:
        raise YearDatasetError("expanded schedule database failed content verification")

    daily_entries: list[dict[str, object]] = []
    total_candidates = 0
    total_outcomes = 0
    total_episodes = 0
    for service_date in service_dates:
        observations = _load_observations(
            service_date, indexed=indexed, normalized_root=normalized_root
        )
        observations_by_id = {
            observation.observation_id: observation for observation in observations
        }
        episodes = build_trip_episodes(observations)
        schedule = match_episodes_to_schedule(
            schedule_database,
            expanded_database_sha256=database_hash,
            episodes=episodes.episodes,
            observations_by_id=observations_by_id,
        )
        targets = build_downstream_examples(schedule.matches, observations_by_id)
        records = build_daily_records(episodes, schedule, targets, observations_by_id)
        candidate_path, candidate_sha256, candidate_bytes = _write_content_addressed_parquet(
            dataset_root / "unsampled/candidates" / f"service_date={service_date.isoformat()}",
            "candidates",
            records.candidate_rows,
            CANDIDATE_SCHEMA,
        )
        outcome_path, outcome_sha256, outcome_bytes = _write_content_addressed_parquet(
            dataset_root
            / (
                "sealed-final-outcomes"
                if chronological_split(service_date) is DatasetSplit.FINAL_TEST
                else "unsampled/outcomes"
            )
            / f"service_date={service_date.isoformat()}",
            "outcomes",
            records.outcome_rows,
            OUTCOME_SCHEMA,
        )
        daily_entries.append(
            {
                "audit_projection": records.audit,
                "candidate_index": {
                    "bytes": candidate_bytes,
                    "path": _relative(candidate_path, dataset_root),
                    "row_count": len(records.candidate_rows),
                    "sha256": candidate_sha256,
                },
                "outcomes": {
                    "bytes": outcome_bytes,
                    "path": _relative(outcome_path, dataset_root),
                    "row_count": len(records.outcome_rows),
                    "sealed": chronological_split(service_date) is DatasetSplit.FINAL_TEST,
                    "sha256": outcome_sha256,
                },
                "service_date": service_date.isoformat(),
                "split": chronological_split(service_date).value,
            }
        )
        total_candidates += len(records.candidate_rows)
        total_outcomes += len(records.outcome_rows)
        total_episodes += _required_int(records.audit, "episode_count")

    manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "daily_partitions": daily_entries,
        "dataset_version": DATASET_VERSION,
        "invariants": {
            "candidate_index_contains_no_outcome_state_or_bounds": True,
            "final_test_audit_projection_contains_only_aggregate_support": True,
            "final_test_duration_bounds_are_sealed": True,
            "feature_and_outcome_construction_remain_separate": True,
            "service_date_split_is_exclusive": True,
        },
        "normalized_manifest_sha256": normalized_manifest_sha256,
        "schedule_database_sha256": database_hash,
        "summary": {
            "candidate_example_count": total_candidates,
            "episode_count": total_episodes,
            "outcome_example_count": total_outcomes,
            "service_day_count": len(daily_entries),
        },
        "year": YEAR,
    }
    manifest_path, manifest_sha256 = _write_content_addressed_json(
        dataset_root / "manifests", "unsampled-audit-manifest", manifest
    )
    _write_atomic_json(
        dataset_root / "manifests/active-unsampled.json",
        {
            "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
            "path": _relative(manifest_path, dataset_root),
            "sha256": manifest_sha256,
        },
    )
    runtime = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "elapsed_seconds": time.monotonic() - started,
        "manifest_path": _relative(manifest_path, dataset_root),
        "manifest_sha256": manifest_sha256,
        "maximum_concurrent_service_dates": 1,
        "year": YEAR,
    }
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_root / "unsampled-audit-run.json"
    runtime_path.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return UnsampledAuditResult(
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        candidate_example_count=total_candidates,
        outcome_example_count=total_outcomes,
        episode_count=total_episodes,
        runtime_report_path=runtime_path,
    )


def build_unsampled_audit(
    *,
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    schedule_database: Path = DEFAULT_SCHEDULE_DATABASE,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> UnsampledAuditResult:
    """Build the complete unsampled population audit without exposing final durations."""

    service_dates = tuple(
        date.fromordinal(ordinal)
        for ordinal in range(date(YEAR, 1, 1).toordinal(), date(YEAR, 12, 31).toordinal() + 1)
    )
    return _build_unsampled_audit_for_dates(
        service_dates,
        normalized_root=normalized_root,
        dataset_root=dataset_root,
        schedule_database=schedule_database,
        runtime_root=runtime_root,
    )
