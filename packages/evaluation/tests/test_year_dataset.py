from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_data_contracts.travel_time import (
    EpisodeScheduleMatchStatus,
    HistoricalVehicleStatus,
    SourceLineageEntry,
    TripScheduleRelationship,
    VehicleObservation,
    vehicle_observation_id,
)
from arrive90_evaluation.model_population import PopulationBuildResult
from arrive90_evaluation.year_dataset import (
    OUTCOME_SCHEMA,
    FinalTestOutcomeAccessError,
    UnsampledAuditResult,
    YearDatasetError,
    _build_unsampled_audit_for_dates,
    _counter,
    _load_json,
    _load_observations,
    _normalized_manifest,
    _partition_index,
    _required_int,
    _schedule_database_hash,
    build_daily_records,
    read_outcome_partition,
)
from arrive90_ingestion.cli import main
from arrive90_ingestion.episodes import EpisodeBuildResult, build_trip_episodes
from arrive90_ingestion.historical_schedule import (
    EpisodeScheduleMatch,
    ScheduledStop,
    ScheduledTrip,
    ScheduleMatchReason,
    ScheduleMatchResult,
)
from arrive90_outcomes.travel_time import TargetBuildResult, build_downstream_examples

SERVICE_DATE = date(2024, 11, 1)
START = datetime(2024, 11, 1, 12, tzinfo=UTC)


def _observation(
    seconds: int, sequence: int, status: HistoricalVehicleStatus
) -> VehicleObservation:
    observed = START + timedelta(seconds=seconds)
    identifier = vehicle_observation_id(
        trip_start_date=SERVICE_DATE,
        trip_start_time="08:00:00",
        trip_id="trip-1",
        route_id="Red",
        direction_id=0,
        vehicle_id="vehicle-1",
        observation_utc=observed,
        stop_sequence=sequence,
        current_status=status,
    )
    return VehicleObservation(
        observation_id=identifier,
        source_lineage=(SourceLineageEntry("source.parquet", seconds + sequence),),
        entity_id="entity",
        trip_id="trip-1",
        trip_start_date=SERVICE_DATE,
        trip_start_time="08:00:00",
        schedule_relationship=TripScheduleRelationship.SCHEDULED,
        route_id="Red",
        direction_id=0,
        vehicle_id="vehicle-1",
        vehicle_label="train",
        observation_source_naive_utc=observed.replace(tzinfo=None),
        observation_utc=observed,
        stop_sequence=sequence,
        stop_id=f"stop-{sequence}",
        current_status=status,
        latitude=None,
        longitude=None,
        bearing=None,
        speed=None,
        schema_version="test-v1",
    )


def _fixture() -> tuple[
    EpisodeBuildResult,
    ScheduleMatchResult,
    TargetBuildResult,
    dict[str, VehicleObservation],
]:
    observations = (
        _observation(0, 1, HistoricalVehicleStatus.STOPPED_AT),
        _observation(60, 10, HistoricalVehicleStatus.INCOMING_AT),
        _observation(120, 10, HistoricalVehicleStatus.STOPPED_AT),
    )
    episode_result = build_trip_episodes(observations)
    episode = replace(
        episode_result.episodes[0],
        schedule_match_status=EpisodeScheduleMatchStatus.EXACT_MATCH,
        schedule_version_id="schedule-v1",
        route_pattern_id="pattern-v1",
    )
    stops = tuple(
        ScheduledStop(
            stop_id=f"stop-{sequence}",
            stop_sequence=sequence,
            arrival_local_seconds=8 * 3_600 + seconds,
            departure_local_seconds=8 * 3_600 + seconds,
            arrival_utc=START + timedelta(seconds=seconds),
            departure_utc=START + timedelta(seconds=seconds),
        )
        for sequence, seconds in ((1, 0), (10, 300))
    )
    trip = ScheduledTrip(
        schedule_version_id="schedule-v1",
        feed_version="Fall, 2024-10-01T00:00:00+00:00, A",
        published_at_utc=datetime(2024, 10, 1, tzinfo=UTC),
        service_date=SERVICE_DATE,
        service_id="weekday",
        trip_id="trip-1",
        route_id="Red",
        direction_id=0,
        route_pattern_id="pattern-v1",
        trip_start_time="08:00:00",
        stops=stops,
    )
    match = EpisodeScheduleMatch(episode, ScheduleMatchReason.EXACT, trip)
    schedule = ScheduleMatchResult(
        schedule_days=(),
        matches=(match,),
        reason_counts=((ScheduleMatchReason.EXACT.value, 1),),
    )
    by_id = {observation.observation_id: observation for observation in observations}
    targets = build_downstream_examples(schedule.matches, by_id)
    return episode_result, schedule, targets, by_id


def test_daily_records_separate_candidate_projection_and_outcome_bounds() -> None:
    episode_result, schedule, targets, observations = _fixture()
    records = build_daily_records(episode_result, schedule, targets, observations)
    assert len(records.candidate_rows) == 1
    assert len(records.outcome_rows) == 1
    assert "outcome_state" not in records.candidate_rows[0]
    assert "lower_bound_seconds" not in records.candidate_rows[0]
    assert records.outcome_rows[0]["lower_bound_seconds"] == 60
    assert records.outcome_rows[0]["upper_bound_seconds"] == 120
    assert records.audit["split"] == DatasetSplit.FINAL_TEST.value
    assert "lower_bound_seconds" not in str(records.audit)
    retention_cell = records.audit["retention_cells"][0]  # type: ignore[index]
    assert retention_cell["likelihood_example_count"] == 1
    assert retention_cell["finite_width_pass_count"] == 1
    schedule_cell = records.audit["schedule_cells"][0]  # type: ignore[index]
    assert schedule_cell["counts"]["scheduled_episode_count"] == 1
    assert schedule_cell["counts"]["scheduled_reason:EXACT"] == 1


def test_final_test_outcome_reader_fails_before_milestone_four(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=OUTCOME_SCHEMA), path)
    with pytest.raises(FinalTestOutcomeAccessError, match="sealed"):
        read_outcome_partition(
            path,
            split=DatasetSplit.FINAL_TEST,
            requesting_milestone=2,
        )
    assert (
        read_outcome_partition(
            path,
            split=DatasetSplit.FINAL_TEST,
            requesting_milestone=4,
        ).num_rows
        == 0
    )


def test_public_cli_dispatches_full_year_dataset_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = UnsampledAuditResult(
        manifest_path=tmp_path / "manifest.json",
        manifest_sha256="a" * 64,
        candidate_example_count=10,
        outcome_example_count=12,
        episode_count=4,
        runtime_report_path=tmp_path / "runtime.json",
    )
    monkeypatch.setattr(
        "arrive90_evaluation.year_dataset.build_unsampled_audit",
        lambda **_kwargs: result,
    )
    population = PopulationBuildResult(
        manifest_path=tmp_path / "population.json",
        manifest_sha256="b" * 64,
        selected_anchor_count=5,
        selected_example_count=8,
        benchmark_report_path=tmp_path / "benchmark.json",
        runtime_report_path=tmp_path / "population-runtime.json",
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.build_model_population",
        lambda **_kwargs: population,
    )
    assert main(["data", "build-dataset"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unsampled_manifest_sha256"] == "a" * 64
    assert payload["unsampled_candidate_example_count"] == 10
    assert payload["model_population_manifest_sha256"] == "b" * 64
    assert payload["selected_anchor_count"] == 5

    assert main(["data", "build-dataset", "--unsampled-only"]) == 0
    unsampled_payload = json.loads(capsys.readouterr().out)
    assert unsampled_payload["manifest_sha256"] == "a" * 64
    assert unsampled_payload["candidate_example_count"] == 10

    assert main(["data", "build-dataset", "--population-only"]) == 0
    population_payload = json.loads(capsys.readouterr().out)
    assert population_payload["unsampled_manifest_sha256"] is None
    assert population_payload["model_population_manifest_sha256"] == "b" * 64


def test_date_scoped_orchestration_writes_seals_and_restarts_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_result, schedule, _targets, observations = _fixture()
    normalized_root = tmp_path / "normalized"
    dataset_root = tmp_path / "datasets"
    schedule_database = tmp_path / "schedule.db"
    schedule_database.write_bytes(b"schedule")
    schedule_hash = hashlib.sha256(b"schedule").hexdigest()
    monkeypatch.setattr(
        "arrive90_evaluation.year_dataset._normalized_manifest",
        lambda _root: (tmp_path / "normalized.json", {}, "b" * 64),
    )
    monkeypatch.setattr("arrive90_evaluation.year_dataset._partition_index", lambda _manifest: {})
    monkeypatch.setattr(
        "arrive90_evaluation.year_dataset._schedule_database_hash",
        lambda _manifest, _root: schedule_hash,
    )
    monkeypatch.setattr(
        "arrive90_evaluation.year_dataset._load_observations",
        lambda *_args, **_kwargs: tuple(observations.values()),
    )
    monkeypatch.setattr(
        "arrive90_evaluation.year_dataset.match_episodes_to_schedule",
        lambda *_args, **_kwargs: schedule,
    )
    first = _build_unsampled_audit_for_dates(
        (SERVICE_DATE,),
        normalized_root=normalized_root,
        dataset_root=dataset_root,
        schedule_database=schedule_database,
        runtime_root=tmp_path / "runtime-first",
    )
    second = _build_unsampled_audit_for_dates(
        (SERVICE_DATE,),
        normalized_root=normalized_root,
        dataset_root=dataset_root,
        schedule_database=schedule_database,
        runtime_root=tmp_path / "runtime-second",
    )
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.episode_count == len(episode_result.episodes)
    assert first.candidate_example_count == 1
    assert first.outcome_example_count == 1
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    daily = manifest["daily_partitions"][0]
    assert daily["outcomes"]["sealed"] is True
    assert (dataset_root / daily["candidate_index"]["path"]).is_file()
    sealed_path = dataset_root / daily["outcomes"]["path"]
    with pytest.raises(FinalTestOutcomeAccessError):
        read_outcome_partition(
            sealed_path,
            split=DatasetSplit.FINAL_TEST,
            requesting_milestone=2,
        )


def test_date_scoped_orchestration_rejects_invalid_date_sets(tmp_path: Path) -> None:
    with pytest.raises(YearDatasetError, match="nonempty"):
        _build_unsampled_audit_for_dates((), dataset_root=tmp_path)
    with pytest.raises(YearDatasetError, match="inside 2024"):
        _build_unsampled_audit_for_dates((date(2025, 1, 1),), dataset_root=tmp_path)


def test_normalized_manifest_partition_and_schedule_helpers_verify_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_root = tmp_path / "normalized"
    manifest_root = normalized_root / "manifests/2024"
    manifest_root.mkdir(parents=True)
    body = b'{"acceptance_version":"travel-time-v1.2"}'
    digest = hashlib.sha256(body).hexdigest()
    path = manifest_root / f"dataset-manifest-{digest}.json"
    path.write_bytes(body)
    stale_body = b'{"acceptance_version":"travel-time-v1.1"}'
    stale_digest = hashlib.sha256(stale_body).hexdigest()
    (manifest_root / f"dataset-manifest-{stale_digest}.json").write_bytes(stale_body)
    observed_path, observed_manifest, observed_digest = _normalized_manifest(normalized_root)
    assert observed_path == path
    assert observed_manifest["acceptance_version"] == "travel-time-v1.2"
    assert observed_digest == digest

    partition_rows = [
        {
            "path": f"vehicle/{route}/{service_date}.parquet",
            "route_id": route,
            "service_date": service_date.isoformat(),
            "sha256": "a" * 64,
        }
        for route in ("Blue", "Orange", "Red")
        for service_date in (
            date.fromordinal(ordinal)
            for ordinal in range(date(2024, 1, 1).toordinal(), date(2024, 12, 31).toordinal() + 1)
        )
    ]
    indexed = _partition_index({"partitions": partition_rows})
    assert len(indexed) == 1_098
    assert ("Red", date(2024, 12, 31)) in indexed

    schedule_body = b'{"database_sha256":"' + b"b" * 64 + b'"}'
    schedule_path = normalized_root / "schedule/index.json"
    schedule_path.parent.mkdir(parents=True)
    schedule_path.write_bytes(schedule_body)
    schedule_sha = hashlib.sha256(schedule_body).hexdigest()
    schedule_manifest = {"schedule_index": {"path": "schedule/index.json", "sha256": schedule_sha}}
    assert _schedule_database_hash(schedule_manifest, normalized_root) == "b" * 64

    fixture_observations = tuple(_fixture()[3].values())
    day = date(2024, 11, 1)
    day_index: dict[tuple[str, date], dict[str, object]] = {}
    for route in ("Blue", "Orange", "Red"):
        source = normalized_root / f"{route}.parquet"
        source.write_bytes(route.encode())
        day_index[(route, day)] = {
            "path": source.relative_to(normalized_root).as_posix(),
            "sha256": hashlib.sha256(route.encode()).hexdigest(),
        }
    values = iter((fixture_observations, (), ()))
    monkeypatch.setattr(
        "arrive90_evaluation.year_dataset.read_normalized_partition",
        lambda _path: next(values),
    )
    assert _load_observations(day, indexed=day_index, normalized_root=normalized_root) == (
        fixture_observations
    )


def test_normalized_manifest_and_partition_helpers_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(YearDatasetError, match="exactly one"):
        _normalized_manifest(tmp_path)
    with pytest.raises(YearDatasetError, match="list"):
        _partition_index({})
    with pytest.raises(YearDatasetError, match="coverage"):
        _partition_index({"partitions": []})
    with pytest.raises(YearDatasetError, match="schedule index"):
        _schedule_database_hash({}, tmp_path)


def test_json_integer_and_counter_helpers_fail_closed(tmp_path: Path) -> None:
    non_object = tmp_path / "array.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(YearDatasetError, match="JSON object"):
        _load_json(non_object)
    for invalid_integer in (None, True, "1"):
        with pytest.raises(YearDatasetError, match="must be an integer"):
            _required_int({"value": invalid_integer}, "value")
    with pytest.raises(YearDatasetError, match="count mapping"):
        _counter([], "counts")
    for invalid_counter in ({1: 1}, {"one": True}, {"one": "1"}):
        with pytest.raises(YearDatasetError, match="string integer pairs"):
            _counter(invalid_counter, "counts")


def test_manifest_partition_and_schedule_verifiers_reject_corruption(tmp_path: Path) -> None:
    normalized_root = tmp_path / "normalized"
    manifest_root = normalized_root / "manifests/2024"
    manifest_root.mkdir(parents=True)
    wrong_name = manifest_root / f"dataset-manifest-{'a' * 64}.json"
    wrong_name.write_text('{"acceptance_version":"travel-time-v1.2"}', encoding="utf-8")
    with pytest.raises(YearDatasetError, match="filename"):
        _normalized_manifest(normalized_root)

    wrong_name.unlink()
    stale_body = b'{"acceptance_version":"stale"}'
    stale_digest = hashlib.sha256(stale_body).hexdigest()
    (manifest_root / f"dataset-manifest-{stale_digest}.json").write_bytes(stale_body)
    with pytest.raises(YearDatasetError, match="exactly one active"):
        _normalized_manifest(normalized_root)

    with pytest.raises(YearDatasetError, match="entries"):
        _partition_index({"partitions": [None]})
    duplicate = {"route_id": "Red", "service_date": "2024-01-01"}
    with pytest.raises(YearDatasetError, match="unique"):
        _partition_index({"partitions": [duplicate, duplicate]})

    index_path = normalized_root / "schedule/index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text('{"database_sha256":"bad"}', encoding="utf-8")
    with pytest.raises(YearDatasetError, match="content verification"):
        _schedule_database_hash(
            {"schedule_index": {"path": "schedule/index.json", "sha256": "0" * 64}},
            normalized_root,
        )
    index_sha = hashlib.sha256(index_path.read_bytes()).hexdigest()
    with pytest.raises(YearDatasetError, match="database hash"):
        _schedule_database_hash(
            {"schedule_index": {"path": "schedule/index.json", "sha256": index_sha}},
            normalized_root,
        )
