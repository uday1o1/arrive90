"""Milestone 0 real-source travel-time qualification pipeline."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.travel_time import (
    DownstreamOutcomeState,
    DownstreamStopExample,
    EpisodeQualityFlag,
    HistoricalVehicleStatus,
    VehicleObservation,
)
from arrive90_features.travel_time import (
    FutureObservationAccessError,
    ObservationCutoffView,
    TravelTimeFeatureRow,
    build_travel_time_feature_row,
)
from arrive90_ingestion.acquisition import sha256_file
from arrive90_ingestion.episodes import build_trip_episodes, stopped_sequences
from arrive90_ingestion.historical import canonical_json_bytes
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    match_episodes_to_schedule,
)
from arrive90_ingestion.vehicle import OBSERVATION_TIMESTAMP, normalize_vehicle_parquet
from arrive90_outcomes.travel_time import build_downstream_examples

QUALIFICATION_VERSION = "arrive90-milestone0-travel-time-v1"
PINNED_DATE = date(2024, 5, 15)


class QualificationError(ValueError):
    """Raised when qualification inputs or output paths violate the frozen contract."""


@dataclass(frozen=True, slots=True)
class QualificationRun:
    """Paths, hashes, and check evidence from one deterministic run."""

    normalized_manifest_path: Path
    normalized_manifest_sha256: str
    example_manifest_path: Path
    example_manifest_sha256: str
    run_summary_path: Path
    run_summary_sha256: str
    checks_passed: bool


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise QualificationError(f"{field} must be a mapping with string keys")
    return value


def _load_yaml(path: Path, field: str) -> dict[str, Any]:
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), field)


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise QualificationError(f"{field} must be valid JSON") from error
    return _mapping(loaded, field)


def _records_sha256(records: Iterable[Any]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(canonical_json_bytes(asdict(record)))
    return digest.hexdigest()


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationError(f"{field} must be numeric")
    return float(value)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QualificationError(f"{field} must be an integer")
    return value


def _write_immutable(path: Path, payload: object) -> str:
    body = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise QualificationError(f"qualification output has conflicting bytes: {path}")
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _check(
    checks: list[dict[str, object]],
    name: str,
    passed: bool,
    observed: object,
    required: object,
) -> None:
    checks.append(
        {
            "name": name,
            "observed": observed,
            "passed": bool(passed),
            "required": required,
        }
    )


def _whole_object_range(path: Path) -> tuple[datetime, datetime]:
    column = pq.read_table(path, columns=[OBSERVATION_TIMESTAMP])[OBSERVATION_TIMESTAMP]
    minimum = pc.min(column).as_py()
    maximum = pc.max(column).as_py()
    if not isinstance(minimum, datetime) or not isinstance(maximum, datetime):
        raise QualificationError("vehicle source has no valid observation range")
    return minimum, maximum


def _episode_support(
    matches: tuple[EpisodeScheduleMatch, ...],
    observations_by_id: dict[str, VehicleObservation],
) -> dict[str, dict[str, object]]:
    report: dict[str, dict[str, object]] = {}
    for route_id in sorted({match.episode.route_id for match in matches}):
        episodes = [match.episode for match in matches if match.episode.route_id == route_id]
        all_buckets: Counter[str] = Counter()
        trackable_buckets: Counter[str] = Counter()
        one_observation = 0
        zero_duration = 0
        post_gap = 0
        trackable_count = 0
        for episode in episodes:
            event_times = {
                observations_by_id[identifier].observation_utc
                for identifier in episode.observation_ids
            }
            stopped_count = len(stopped_sequences(episode, observations_by_id))
            bucket = "zero" if stopped_count == 0 else "one" if stopped_count == 1 else "multi"
            all_buckets[bucket] += 1
            trackable = len(event_times) >= 2
            if trackable:
                trackable_count += 1
                trackable_buckets[bucket] += 1
            one_observation += len(episode.observation_ids) == 1
            zero_duration += episode.first_observation_utc == episode.last_observation_utc
            post_gap += EpisodeQualityFlag.EXCESSIVE_GAP in episode.quality_flags
        all_count = len(episodes)
        multi_count = all_buckets["multi"]
        trackable_multi = trackable_buckets["multi"]
        report[route_id] = {
            "all_episode_count": all_count,
            "all_episode_stopped_sequence_bucket_counts": dict(sorted(all_buckets.items())),
            "excluded_episode_count": all_count - trackable_count,
            "gap_split_count": post_gap,
            "one_observation_episode_count": one_observation,
            "post_gap_fragment_count": post_gap,
            "trackable_episode_count": trackable_count,
            "trackable_episode_stopped_sequence_bucket_counts": dict(
                sorted(trackable_buckets.items())
            ),
            "trackable_multi_stop_episode_rate": (
                trackable_multi / trackable_count if trackable_count else 0.0
            ),
            "unconditioned_multi_stop_episode_rate": (
                multi_count / all_count if all_count else 0.0
            ),
            "zero_duration_episode_count": zero_duration,
        }
    return report


def _example_metrics(
    matches: tuple[EpisodeScheduleMatch, ...],
    examples: tuple[DownstreamStopExample, ...],
) -> dict[str, dict[str, object]]:
    route_by_episode = {match.episode.episode_id: match.episode.route_id for match in matches}
    state_counts: dict[str, Counter[str]] = defaultdict(Counter)
    finite_widths: dict[str, list[float]] = defaultdict(list)
    for example in examples:
        route_id = route_by_episode[example.episode_id]
        state_counts[route_id][example.outcome_state.value] += 1
        if example.outcome_state in {
            DownstreamOutcomeState.INTERVAL_RESOLVED,
            DownstreamOutcomeState.LEFT_CENSORED,
            DownstreamOutcomeState.OVER_WIDTH_INTERVAL,
        }:
            if example.lower_bound_seconds is None or example.upper_bound_seconds is None:
                raise QualificationError("finite example is missing interval bounds")
            finite_widths[route_id].append(
                example.upper_bound_seconds - example.lower_bound_seconds
            )
    metrics: dict[str, dict[str, object]] = {}
    for route_id in sorted(state_counts):
        counts = state_counts[route_id]
        widths = finite_widths[route_id]
        metrics[route_id] = {
            "finite_interval_count": len(widths),
            "finite_interval_width_coverage": (
                sum(width <= 180 for width in widths) / len(widths) if widths else 0.0
            ),
            "finite_or_left_censored_count": (
                counts[DownstreamOutcomeState.INTERVAL_RESOLVED.value]
                + counts[DownstreamOutcomeState.LEFT_CENSORED.value]
            ),
            "outcome_state_counts": dict(sorted(counts.items())),
        }
    return metrics


def _finite_upper_integrity(
    matches: tuple[EpisodeScheduleMatch, ...],
    examples: tuple[DownstreamStopExample, ...],
    observations_by_id: dict[str, VehicleObservation],
) -> bool:
    observation_ids = {
        match.episode.episode_id: set(match.episode.observation_ids) for match in matches
    }
    for example in examples:
        if example.outcome_state not in {
            DownstreamOutcomeState.INTERVAL_RESOLVED,
            DownstreamOutcomeState.LEFT_CENSORED,
            DownstreamOutcomeState.OVER_WIDTH_INTERVAL,
        }:
            continue
        upper_id = example.upper_evidence_observation_id
        if upper_id is None or upper_id not in observation_ids[example.episode_id]:
            return False
        upper = observations_by_id[upper_id]
        if (
            upper.observation_utc <= example.feature_cutoff_utc
            or upper.current_status is not HistoricalVehicleStatus.STOPPED_AT
            or upper.stop_id != example.destination_stop_id
            or upper.stop_sequence != example.destination_stop_sequence
        ):
            return False
    return True


def _feature_probe(
    matches: tuple[EpisodeScheduleMatch, ...],
    examples: tuple[DownstreamStopExample, ...],
    observations_by_id: dict[str, VehicleObservation],
) -> tuple[TravelTimeFeatureRow | None, bool, EpisodeScheduleMatch | None]:
    match_by_episode = {match.episode.episode_id: match for match in matches}
    for example in examples:
        if not example.included_in_likelihood or example.destination_stop_id is None:
            continue
        match = match_by_episode[example.episode_id]
        later = [
            observations_by_id[identifier]
            for identifier in match.episode.observation_ids
            if observations_by_id[identifier].observation_utc > example.feature_cutoff_utc
        ]
        if not later:
            continue
        view = ObservationCutoffView.from_episode(
            match.episode,
            observations_by_id,
            cutoff_utc=example.feature_cutoff_utc,
        )
        if (
            example.destination_stop_sequence is None
            or example.destination_offset is None
            or example.scheduled_remaining_seconds is None
        ):
            raise QualificationError("matched example has incomplete destination fields")
        row = build_travel_time_feature_row(
            match,
            view,
            anchor_observation_id=example.anchor_observation_id,
            destination_stop_id=example.destination_stop_id,
            destination_stop_sequence=example.destination_stop_sequence,
            destination_offset=example.destination_offset,
            scheduled_remaining_seconds=example.scheduled_remaining_seconds,
        )
        rejected = False
        try:
            view.observation(later[0].observation_id)
        except FutureObservationAccessError:
            rejected = True
        return row, rejected, match
    return None, False, None


def qualify_day(
    inventory_date: date,
    *,
    raw_root: Path,
    bus_profile_path: Path,
    schedule_profile_path: Path,
    acquisition_lock_path: Path,
    acceptance_charter_path: Path,
    runtime_root: Path,
) -> QualificationRun:
    """Run the complete pinned-day qualification and write deterministic manifests."""

    if inventory_date != PINNED_DATE:
        raise QualificationError(f"Milestone 0 qualification is pinned to {PINNED_DATE}")
    charter = _load_yaml(acceptance_charter_path, "acceptance charter")
    bus_profile = _load_yaml(bus_profile_path, "Bus Observatory profile")
    schedule_profile = _load_yaml(schedule_profile_path, "schedule profile")
    acquisition_lock = _load_json(acquisition_lock_path, "acquisition lock")
    for name, envelope in (
        ("acceptance charter", charter),
        ("Bus Observatory profile", bus_profile),
        ("schedule profile", schedule_profile),
        ("acquisition lock", acquisition_lock),
    ):
        if envelope.get("acceptance_version") != DEFAULT_ACCEPTANCE_VERSION:
            raise QualificationError(f"{name} has a stale acceptance version")

    sample = _mapping(bus_profile.get("sample"), "Bus Observatory sample")
    source_url = str(sample["url"])
    source_object_key = source_url.split(".com/", maxsplit=1)[-1]
    vehicle_path = raw_root / "bus-observatory" / "mbta_all" / Path(source_object_key).name
    schedule_archive_path = raw_root / "mbta-gtfs" / "2024" / "GTFS_ARCHIVE.db.gz"
    schedule_database_path = raw_root / "mbta-gtfs" / "2024" / "GTFS_ARCHIVE.db"
    for path in (vehicle_path, schedule_archive_path, schedule_database_path):
        if not path.is_file():
            raise QualificationError(
                f"required source is missing: {path}; run arrive90 source download first"
            )

    content_entries = acquisition_lock.get("content_entries")
    derived_entries = acquisition_lock.get("derived_entries")
    if not isinstance(content_entries, list) or not isinstance(derived_entries, list):
        raise QualificationError("acquisition lock entries must be lists")
    content_by_key = {
        str(_mapping(entry, "content entry")["source_object_key"]): _mapping(entry, "content entry")
        for entry in content_entries
    }
    vehicle_lock = content_by_key[source_object_key]
    schedule_lock = next(
        _mapping(entry, "schedule content entry")
        for entry in content_entries
        if str(_mapping(entry, "schedule content entry")["source_object_key"]).endswith(
            "GTFS_ARCHIVE.db.gz"
        )
    )
    derived_lock = _mapping(derived_entries[0], "schedule derived entry")
    expanded_sha256 = str(derived_lock["output_sha256"])

    normalized = normalize_vehicle_parquet(vehicle_path, source_object_key=source_object_key)
    repeated_episodes = build_trip_episodes(normalized.observations)
    episodes = build_trip_episodes(normalized.observations)
    observations_by_id = {
        observation.observation_id: observation for observation in normalized.observations
    }
    schedule_matches = match_episodes_to_schedule(
        schedule_database_path,
        expanded_database_sha256=expanded_sha256,
        episodes=episodes.episodes,
        observations_by_id=observations_by_id,
    )
    repeated_matches = match_episodes_to_schedule(
        schedule_database_path,
        expanded_database_sha256=expanded_sha256,
        episodes=episodes.episodes,
        observations_by_id=observations_by_id,
    )
    targets = build_downstream_examples(schedule_matches.matches, observations_by_id)
    feature_row, future_access_rejected, feature_match = _feature_probe(
        schedule_matches.matches, targets.examples, observations_by_id
    )
    episode_support = _episode_support(schedule_matches.matches, observations_by_id)
    example_metrics = _example_metrics(schedule_matches.matches, targets.examples)
    whole_minimum, whole_maximum = _whole_object_range(vehicle_path)

    normalized_manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "episode_count": len(episodes.episodes),
        "episode_records_sha256": _records_sha256(episodes.episodes),
        "episode_support_by_route": episode_support,
        "exact_duplicate_row_count": normalized.exact_duplicate_row_count,
        "identity_availability_by_route": dict(normalized.identity_availability_by_route),
        "identity_availability_overall": normalized.identity_availability_overall,
        "normalization_version": QUALIFICATION_VERSION,
        "observation_count": len(normalized.observations),
        "observation_records_sha256": _records_sha256(normalized.observations),
        "parser_version": normalized.parser_version,
        "quarantined_record_count": len(normalized.quarantined_rows),
        "quarantined_records_sha256": _records_sha256(normalized.quarantined_rows),
        "retained_raw_row_count": normalized.retained_raw_row_count,
        "retained_rows_by_route": dict(normalized.retained_rows_by_route),
        "source_object_key": source_object_key,
        "source_row_count": normalized.source_row_count,
        "source_schema_fingerprint": normalized.source_schema_fingerprint,
        "source_sha256": sha256_file(vehicle_path),
        "source_whole_max_naive_utc": whole_maximum.isoformat(sep=" "),
        "source_whole_min_naive_utc": whole_minimum.isoformat(sep=" "),
    }
    normalized_path = runtime_root / "normalized-manifest.json"
    normalized_sha256 = _write_immutable(normalized_path, normalized_manifest)

    example_manifest = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "anchor_count": targets.anchor_count,
        "example_count": len(targets.examples),
        "example_metrics_by_route": example_metrics,
        "example_records_sha256": _records_sha256(targets.examples),
        "feature_probe": asdict(feature_row) if feature_row is not None else None,
        "matched_anchor_count": targets.matched_anchor_count,
        "outcome_state_counts": dict(targets.outcome_state_counts),
        "schedule_days_sha256": _records_sha256(schedule_matches.schedule_days),
        "schedule_match_reason_counts": dict(schedule_matches.reason_counts),
        "schedule_match_records_sha256": _records_sha256(schedule_matches.matches),
        "schedule_versions": [asdict(day.version) for day in schedule_matches.schedule_days],
        "target_version": QUALIFICATION_VERSION,
        "terminal_anchor_count": targets.terminal_anchor_count,
    }
    example_path = runtime_root / "example-manifest.json"
    example_sha256 = _write_immutable(example_path, example_manifest)

    gate = _mapping(charter.get("one_day_gate"), "one-day gate")
    required_routes = [str(route) for route in gate["required_routes"]]
    checks: list[dict[str, object]] = []
    _check(
        checks,
        "vehicle_source_lock",
        (
            normalized_manifest["source_sha256"] == vehicle_lock["sha256"] == sample["sha256"]
            and normalized.source_row_count == vehicle_lock["row_count"] == sample["row_count"]
            and normalized.source_schema_fingerprint
            == vehicle_lock["schema_fingerprint"]
            == sample["schema_fingerprint"]
            and vehicle_path.stat().st_size
            == vehicle_lock["response_size_bytes"]
            == sample["size_bytes"]
            and normalized_manifest["source_whole_min_naive_utc"]
            == sample["whole_object_min_naive_utc"]
            and normalized_manifest["source_whole_max_naive_utc"]
            == sample["whole_object_max_naive_utc"]
        ),
        {
            "rows": normalized.source_row_count,
            "sha256": normalized_manifest["source_sha256"],
            "size_bytes": vehicle_path.stat().st_size,
        },
        "exact pinned hash, size, rows, schema, and range",
    )
    _check(
        checks,
        "schedule_source_and_derived_locks",
        (
            sha256_file(schedule_archive_path) == schedule_lock["sha256"]
            and schedule_archive_path.stat().st_size == schedule_lock["response_size_bytes"]
            and sha256_file(schedule_database_path) == derived_lock["output_sha256"]
            and schedule_database_path.stat().st_size == derived_lock["output_size_bytes"]
            and schedule_matches.schedule_days == repeated_matches.schedule_days
        ),
        {
            "archive_sha256": schedule_lock["sha256"],
            "database_sha256": expanded_sha256,
            "schedule_version_count": len(schedule_matches.schedule_days),
        },
        "exact compressed and derived locks plus deterministic version lookup",
    )
    present_routes = sorted(
        route for route, count in normalized.retained_rows_by_route if count > 0
    )
    _check(
        checks,
        "required_routes_present",
        present_routes == sorted(required_routes),
        present_routes,
        required_routes,
    )
    overall_min = float(gate["identity_availability_overall_min"])
    _check(
        checks,
        "identity_availability_overall",
        normalized.identity_availability_overall >= overall_min,
        normalized.identity_availability_overall,
        overall_min,
    )
    line_identity_min = float(gate["identity_availability_per_line_min"])
    identity_by_route = dict(normalized.identity_availability_by_route)
    support_min = float(gate["trackable_multi_stop_episode_rate_per_line_min"])
    finite_min = int(gate["finite_examples_per_line_min"])
    width_min = float(gate["finite_interval_width_coverage_per_line_min"])
    for route_id in required_routes:
        _check(
            checks,
            f"identity_availability_{route_id}",
            identity_by_route.get(route_id, 0.0) >= line_identity_min,
            identity_by_route.get(route_id, 0.0),
            line_identity_min,
        )
        support = _number(
            episode_support.get(route_id, {}).get("trackable_multi_stop_episode_rate", 0.0),
            f"trackable multi-stop episode rate for {route_id}",
        )
        _check(
            checks,
            f"trackable_multi_stop_rate_{route_id}",
            support >= support_min,
            support,
            support_min,
        )
        finite_count = _integer(
            example_metrics.get(route_id, {}).get("finite_or_left_censored_count", 0),
            f"finite example count for {route_id}",
        )
        _check(
            checks,
            f"finite_examples_{route_id}",
            finite_count >= finite_min,
            finite_count,
            finite_min,
        )
        width_coverage = _number(
            example_metrics.get(route_id, {}).get("finite_interval_width_coverage", 0.0),
            f"finite interval width coverage for {route_id}",
        )
        _check(
            checks,
            f"finite_interval_width_{route_id}",
            width_coverage >= width_min,
            width_coverage,
            width_min,
        )
    _check(
        checks,
        "episode_construction_stable",
        episodes == repeated_episodes,
        _records_sha256(episodes.episodes),
        "byte-stable episode records",
    )
    _check(
        checks,
        "deduplicated_lineage_complete",
        all(observation.source_lineage for observation in normalized.observations),
        normalized.exact_duplicate_row_count,
        "every canonical observation has source lineage",
    )
    _check(
        checks,
        "finite_upper_evidence_integrity",
        _finite_upper_integrity(schedule_matches.matches, targets.examples, observations_by_id),
        len(targets.examples),
        "later same-episode destination STOPPED_AT",
    )
    _check(
        checks,
        "feature_cutoff_and_future_access_guard",
        feature_row is not None
        and future_access_rejected
        and feature_row.feature_cutoff_utc
        == observations_by_id[feature_row.anchor_observation_id].observation_utc,
        asdict(feature_row) if feature_row is not None else None,
        "feature cutoff equals anchor and later access raises",
    )
    _check(
        checks,
        "schedule_publication_before_feature_cutoff",
        feature_row is not None
        and feature_match is not None
        and feature_match.scheduled_trip is not None
        and feature_match.scheduled_trip.published_at_utc <= feature_row.feature_cutoff_utc,
        feature_row.schedule_version_id if feature_row is not None else None,
        "schedule publication no later than feature cutoff",
    )
    missing_states_valid = all(
        example.outcome_state is not DownstreamOutcomeState.MISSING_STOP_OBSERVATION
        or (example.lower_bound_seconds is None and example.upper_bound_seconds is None)
        for example in targets.examples
    )
    _check(
        checks,
        "missing_destinations_never_finite",
        missing_states_valid,
        dict(targets.outcome_state_counts),
        "missing destination observations carry no finite bounds",
    )

    run_summary = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "checks": checks,
        "checks_passed": all(bool(check["passed"]) for check in checks),
        "example_manifest_sha256": example_sha256,
        "example_metrics_by_route": example_metrics,
        "normalized_manifest_sha256": normalized_sha256,
        "qualification_version": QUALIFICATION_VERSION,
        "source_date": inventory_date,
    }
    summary_path = runtime_root / "run-summary.json"
    summary_sha256 = _write_immutable(summary_path, run_summary)
    return QualificationRun(
        normalized_manifest_path=normalized_path,
        normalized_manifest_sha256=normalized_sha256,
        example_manifest_path=example_path,
        example_manifest_sha256=example_sha256,
        run_summary_path=summary_path,
        run_summary_sha256=summary_sha256,
        checks_passed=bool(run_summary["checks_passed"]),
    )
