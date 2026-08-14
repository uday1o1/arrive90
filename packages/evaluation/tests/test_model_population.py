from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_data_contracts.dataset import DatasetSplit
from arrive90_evaluation.model_population import (
    AUDITED_ROUTES,
    FEATURE_SCHEMA,
    MODELED_ROUTE,
    SELECTION_LIMIT,
    PopulationBuildResult,
    RetentionResult,
    _active_unsampled_manifest,
    _benchmark_sample,
    _blue_observations,
    _feature_input,
    _feature_rows_for_day,
    _fit_partitioned_transform,
    _run_benchmark,
    _selected_rows,
    assert_final_test_sealed,
    build_model_population,
    evaluate_blue_retention,
)
from arrive90_evaluation.year_dataset import CANDIDATE_SCHEMA, OUTCOME_SCHEMA, YearDatasetError
from arrive90_features.transform import MISSING_TOKEN, UNKNOWN_TOKEN
from arrive90_features.travel_time_registry import TRAVEL_TIME_V1_REGISTRY
from arrive90_ingestion.cli import main
from arrive90_ingestion.historical_schedule import ScheduleMatchReason


def _daily_projection(service_date: date) -> dict[str, object]:
    if service_date <= date(2024, 7, 31):
        split = DatasetSplit.TRAINING.value
    elif service_date <= date(2024, 9, 30):
        split = DatasetSplit.MODEL_VALIDATION.value
    elif service_date <= date(2024, 10, 31):
        split = DatasetSplit.CALIBRATION.value
    else:
        split = DatasetSplit.FINAL_TEST.value
    retention_cells: list[dict[str, object]] = []
    schedule_cells: list[dict[str, object]] = []
    for route in AUDITED_ROUTES:
        for direction in (0, 1):
            schedule_cells.append(
                {
                    "counts": {
                        "episode_count": 100,
                        "reason:EXACT": 100,
                        "scheduled_episode_count": 100,
                        "scheduled_reason:EXACT": 100,
                    },
                    "direction_id": direction,
                    "route_id": route,
                }
            )
            retention_cells.extend(
                {
                    "direction_id": direction,
                    "finite_interval_count": 900,
                    "finite_width_pass_count": 850,
                    "likelihood_distinct_anchor_count": 400,
                    "likelihood_example_count": 800,
                    "outcome_state_counts": {
                        "INTERVAL_RESOLVED": 700,
                        "RIGHT_CENSORED": 100,
                        "NO_FOLLOW_UP": 200,
                    },
                    "peak_period": peak,
                    "right_censored_count": 100,
                    "route_id": route,
                    "total_example_count": 1_000,
                }
                for peak in ("OFF_PEAK", "PEAK")
            )
    return {
        "audit_projection": {
            "episode_count_by_route": dict.fromkeys(AUDITED_ROUTES, 200),
            "retention_cells": retention_cells,
            "schedule_cells": schedule_cells,
        },
        "candidate_index": {"path": "unused", "sha256": "0" * 64},
        "outcomes": {"path": "unused", "sealed": split == DatasetSplit.FINAL_TEST.value},
        "service_date": service_date.isoformat(),
        "split": split,
    }


def _year_manifest() -> dict[str, object]:
    return {
        "acceptance_version": "travel-time-v1.2",
        "daily_partitions": [
            _daily_projection(date.fromordinal(ordinal))
            for ordinal in range(date(2024, 1, 1).toordinal(), date(2024, 12, 31).toordinal() + 1)
        ],
    }


def test_retention_accepts_complete_blue_support_without_duration_values() -> None:
    result = evaluate_blue_retention(_year_manifest())
    assert result.accepted is True
    assert all(result.checks.values())
    assert result.report["modeled_routes"] == [MODELED_ROUTE]
    assert result.report["rejected_routes"] == ["Orange", "Red"]
    assert "lower_bound_seconds" not in json.dumps(result.report)
    assert result.report["blue_likelihood_support_overall"] == pytest.approx(0.8)


def test_retention_fails_closed_on_weak_cell_and_forbidden_duration() -> None:
    weak = _year_manifest()
    for day in cast(list[dict[str, Any]], weak["daily_partitions"]):
        audit = cast(dict[str, Any], day["audit_projection"])
        first_cell = cast(list[dict[str, object]], audit["retention_cells"])[0]
        first_cell["likelihood_example_count"] = 0
    weak_result = evaluate_blue_retention(weak)
    assert weak_result.checks["blue_likelihood_support_per_direction_peak"] is False

    leaked = _year_manifest()
    leaked_day = cast(list[dict[str, Any]], leaked["daily_partitions"])[0]
    leaked_audit = cast(dict[str, object], leaked_day["audit_projection"])
    leaked_audit["lower_bound_seconds"] = 1
    with pytest.raises(YearDatasetError, match="forbidden duration"):
        evaluate_blue_retention(leaked)


def _candidate(
    anchor: int, direction: int, offset: int, *, route: str = "Blue"
) -> dict[str, object]:
    anchor_id = f"anchor-{direction}-{anchor:04d}"
    return {
        "example_id": f"example-{direction}-{anchor:04d}-{offset}",
        "episode_id": f"episode-{direction}-{anchor:04d}",
        "anchor_observation_id": anchor_id,
        "service_date": date(2024, 1, 2),
        "split": DatasetSplit.TRAINING.value,
        "route_id": route,
        "direction_id": direction,
        "feature_cutoff_utc": datetime(2024, 1, 2, 12, tzinfo=UTC),
        "peak_period": "OFF_PEAK",
        "destination_stop_id": f"stop-{offset}",
        "destination_stop_sequence": offset + 1,
        "destination_offset": offset,
        "destination_class": "IMMEDIATE" if offset == 1 else "MEDIUM",
        "scheduled_remaining_seconds": offset * 60,
        "base_weight": 0.5,
        "schedule_version_id": "schedule-v1",
        "route_pattern_id": "pattern-v1",
    }


def test_hmac_selection_caps_each_direction_and_preserves_weights() -> None:
    rows = [
        _candidate(anchor, direction, offset)
        for direction in (0, 1)
        for anchor in range(350)
        for offset in (1, 2)
    ]
    rows.extend(_candidate(anchor, 0, 1, route="Orange") for anchor in range(10))
    table = pa.Table.from_pylist(rows, schema=CANDIDATE_SCHEMA)
    first, report = _selected_rows(table)
    second, _ = _selected_rows(table)
    assert first == second
    assert cast(dict[str, object], report["0"])["selected_anchor_count"] == SELECTION_LIMIT
    assert cast(dict[str, object], report["1"])["selected_anchor_count"] == SELECTION_LIMIT
    assert {row["route_id"] for row in first} == {MODELED_ROUTE}
    assert len({row["anchor_observation_id"] for row in first}) == 600
    by_anchor: dict[str, list[dict[str, object]]] = {}
    for row in first:
        by_anchor.setdefault(str(row["anchor_observation_id"]), []).append(row)
    for anchor_rows in by_anchor.values():
        probability = cast(float, anchor_rows[0]["inclusion_probability"])
        assert math.fsum(cast(float, row["base_weight"]) for row in anchor_rows) == pytest.approx(
            1.0
        )
        assert math.fsum(
            cast(float, row["analysis_weight"]) for row in anchor_rows
        ) == pytest.approx(1.0 / probability)


def test_selection_rejects_broken_anchor_base_weights() -> None:
    rows = [_candidate(1, 0, 1), _candidate(1, 0, 2)]
    rows[1]["base_weight"] = 0.4
    with pytest.raises(YearDatasetError, match="base weights"):
        _selected_rows(pa.Table.from_pylist(rows, schema=CANDIDATE_SCHEMA))


def _feature_row(example_id: str, *, destination: str | None) -> dict[str, object]:
    values: dict[str, object] = {}
    for name, spec in TRAVEL_TIME_V1_REGISTRY.specs.items():
        if spec.value_type == "categorical":
            values[name] = {
                "route_id": MODELED_ROUTE,
                "direction_id": "0",
                "origin_stop_id": "origin",
                "destination_stop_id": destination,
                "route_pattern_id": "pattern",
            }[name]
        elif spec.value_type == "boolean":
            values[name] = False
        elif spec.value_type == "integer":
            values[name] = 1
        elif spec.value_type == "float":
            values[name] = 1.0
        else:
            values[name] = None
    return {
        "example_id": example_id,
        "episode_id": f"episode-{example_id}",
        "anchor_observation_id": f"anchor-{example_id}",
        "service_date": date(2024, 1, 2),
        "split": DatasetSplit.TRAINING.value,
        "base_weight": 1.0,
        "inclusion_probability": 1.0,
        "analysis_weight": 1.0,
        **values,
    }


def test_partitioned_transform_is_training_only_and_schema_stable(tmp_path: Path) -> None:
    rows = [
        _feature_row("a", destination="stop-a"),
        _feature_row("b", destination=None),
    ]
    path = tmp_path / "features.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=FEATURE_SCHEMA), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    entry = {
        "path": path.relative_to(tmp_path).as_posix(),
        "sha256": digest,
        "split": DatasetSplit.TRAINING.value,
    }
    transform = _fit_partitioned_transform([entry], tmp_path)
    vocabularies = dict(transform.vocabularies)
    assert vocabularies["route_id"] == (MISSING_TOKEN, UNKNOWN_TOKEN, MODELED_ROUTE)
    assert "Orange" not in vocabularies["route_id"]
    control = _feature_row("control", destination="stop-new")
    control["split"] = DatasetSplit.MODEL_VALIDATION.value
    matrix = transform.transform((_feature_input(control),))
    assert matrix.shape == (1, len(transform.column_names))
    unknown_column = transform.column_names.index("destination_stop_id=__UNKNOWN__")
    assert matrix[0, unknown_column] == 1


def test_feature_input_rejects_seeded_final_length_and_post_outcome_aggregate() -> None:
    for forbidden in ("final_episode_length", "post_outcome_average_seconds"):
        row = _feature_row("seeded", destination="stop")
        row[forbidden] = 123
        with pytest.raises(ValueError, match="schema mismatch"):
            _feature_input(row)


@pytest.mark.parametrize(
    ("probe", "message"),
    (
        ("FUTURE_OBSERVATION", "future observation"),
        ("FINAL_EPISODE_LENGTH", "final_episode_length"),
        ("FUTURE_SCHEDULE", "future schedule"),
        ("POST_OUTCOME_AGGREGATE", "post_outcome_average_seconds"),
        ("SPLIT_LEAKAGE", "split leakage"),
    ),
)
def test_seeded_defects_fail_through_public_dataset_builder(
    probe: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "data",
                "build-dataset",
                "--qualification-probe",
                probe,
                "--qualification-probe-only",
            ]
        )
        == 1
    )
    assert message in capsys.readouterr().err


def test_public_dataset_builder_control_probe_passes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "data",
                "build-dataset",
                "--qualification-probe",
                "CONTROL",
                "--qualification-probe-only",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"probe": "CONTROL", "state": "PASSED"}


def test_active_manifest_ignores_stale_version_and_fails_on_ambiguity(tmp_path: Path) -> None:
    manifest_root = tmp_path / "manifests"
    manifest_root.mkdir()
    active = _year_manifest()
    active_body = json.dumps(active, sort_keys=True, separators=(",", ":")).encode()
    active_sha = hashlib.sha256(active_body).hexdigest()
    active_path = manifest_root / f"unsampled-audit-manifest-{active_sha}.json"
    active_path.write_bytes(active_body)
    stale = {**active, "acceptance_version": "travel-time-v1.1"}
    stale_body = json.dumps(stale, sort_keys=True, separators=(",", ":")).encode()
    stale_sha = hashlib.sha256(stale_body).hexdigest()
    (manifest_root / f"unsampled-audit-manifest-{stale_sha}.json").write_bytes(stale_body)
    path, _manifest, digest = _active_unsampled_manifest(tmp_path)
    assert path == active_path
    assert digest == active_sha

    duplicate = {**active, "probe": True}
    duplicate_body = json.dumps(duplicate, sort_keys=True, separators=(",", ":")).encode()
    duplicate_sha = hashlib.sha256(duplicate_body).hexdigest()
    (manifest_root / f"unsampled-audit-manifest-{duplicate_sha}.json").write_bytes(duplicate_body)
    with pytest.raises(YearDatasetError, match="exactly one"):
        _active_unsampled_manifest(tmp_path)

    (manifest_root / "active-unsampled.json").write_text(
        json.dumps(
            {
                "acceptance_version": "travel-time-v1.2",
                "path": active_path.relative_to(tmp_path).as_posix(),
                "sha256": active_sha,
            }
        ),
        encoding="utf-8",
    )
    pointer_path, _pointer_manifest, pointer_sha = _active_unsampled_manifest(tmp_path)
    assert pointer_path == active_path
    assert pointer_sha == active_sha


def test_active_manifest_pointer_fails_closed_on_version_escape_and_hash(
    tmp_path: Path,
) -> None:
    pointer = tmp_path / "manifests/active-unsampled.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"acceptance_version": "travel-time-v1.1"}), encoding="utf-8")
    with pytest.raises(YearDatasetError, match="wrong acceptance version"):
        _active_unsampled_manifest(tmp_path)
    pointer.write_text(
        json.dumps(
            {
                "acceptance_version": "travel-time-v1.2",
                "path": "../../outside.json",
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(YearDatasetError, match="escapes"):
        _active_unsampled_manifest(tmp_path)
    pointer.write_text(
        json.dumps(
            {
                "acceptance_version": "travel-time-v1.2",
                "path": "manifests/missing.json",
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(YearDatasetError, match="content verification"):
        _active_unsampled_manifest(tmp_path)


def test_final_test_seal_audit_checks_every_final_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome_path = tmp_path / "sealed.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=OUTCOME_SCHEMA), outcome_path)
    digest = hashlib.sha256(outcome_path.read_bytes()).hexdigest()
    manifest = {
        "daily_partitions": [
            {"service_date": "2024-01-01", "split": DatasetSplit.TRAINING.value},
            {
                "service_date": "2024-11-01",
                "split": DatasetSplit.FINAL_TEST.value,
                "outcomes": {
                    "path": outcome_path.relative_to(tmp_path).as_posix(),
                    "sha256": digest,
                },
            },
        ]
    }
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._active_unsampled_manifest",
        lambda _root: (tmp_path / "manifest.json", manifest, "m" * 64),
    )
    assert_final_test_sealed(tmp_path, requesting_milestone=2)
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.read_outcome_partition",
        lambda *_args, **_kwargs: pa.table({}),
    )
    with pytest.raises(YearDatasetError, match="unexpectedly opened"):
        assert_final_test_sealed(tmp_path, requesting_milestone=2)


def test_model_population_orchestration_writes_content_addressed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root = tmp_path / "dataset"
    normalized_root = tmp_path / "normalized"
    runtime_root = tmp_path / "runtime"
    candidate_path = dataset_root / "unsampled/candidates/day.parquet"
    candidate_path.parent.mkdir(parents=True)
    candidate_rows = [_candidate(1, 0, 1), _candidate(1, 0, 2)]
    pq.write_table(pa.Table.from_pylist(candidate_rows, schema=CANDIDATE_SCHEMA), candidate_path)
    candidate_sha = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    unsampled_path = dataset_root / "manifests/unsampled.json"
    unsampled_path.parent.mkdir(parents=True)
    unsampled_path.write_text("{}", encoding="utf-8")
    unsampled = {
        "acceptance_version": "travel-time-v1.2",
        "normalized_manifest_sha256": "n" * 64,
        "daily_partitions": [
            {
                "candidate_index": {
                    "path": candidate_path.relative_to(dataset_root).as_posix(),
                    "sha256": candidate_sha,
                },
                "outcomes": {"path": "unused", "sealed": False},
                "service_date": "2024-01-02",
                "split": DatasetSplit.TRAINING.value,
            }
        ],
    }
    schedule_database = tmp_path / "schedule.db"
    schedule_database.write_bytes(b"schedule")
    schedule_sha = hashlib.sha256(schedule_database.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._active_unsampled_manifest",
        lambda _root: (unsampled_path, unsampled, "u" * 64),
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._normalized_manifest",
        lambda _root: (tmp_path / "normalized.json", {}, "n" * 64),
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._schedule_database_hash",
        lambda _manifest, _root: schedule_sha,
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.evaluate_blue_retention",
        lambda _manifest: RetentionResult(True, {"blue": True}, {"checks": {"blue": True}}),
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._partition_index", lambda _manifest: {}
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._blue_observations",
        lambda *_args, **_kwargs: (),
    )

    def feature_rows(
        selected_rows: list[dict[str, object]],
        _observations: object,
        **_kwargs: object,
    ) -> list[dict[str, object]]:
        output = []
        for selected in selected_rows:
            row = _feature_row(
                str(selected["example_id"]),
                destination=str(selected["destination_stop_id"]),
            )
            row.update(
                {
                    "episode_id": selected["episode_id"],
                    "anchor_observation_id": selected["anchor_observation_id"],
                    "base_weight": selected["base_weight"],
                    "inclusion_probability": selected["inclusion_probability"],
                    "analysis_weight": selected["analysis_weight"],
                }
            )
            output.append(row)
        return output

    monkeypatch.setattr("arrive90_evaluation.model_population._feature_rows_for_day", feature_rows)
    benchmark = {
        "within_memory_budget": True,
        "within_temporary_storage_budget": True,
    }
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._run_benchmark",
        lambda *_args, **_kwargs: benchmark,
    )
    result = build_model_population(
        normalized_root=normalized_root,
        dataset_root=dataset_root,
        schedule_database=schedule_database,
        runtime_root=runtime_root,
    )
    assert isinstance(result, PopulationBuildResult)
    assert result.selected_anchor_count == 1
    assert result.selected_example_count == 2
    assert result.manifest_path.is_file()
    assert hashlib.sha256(result.manifest_path.read_bytes()).hexdigest() == result.manifest_sha256
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["summary"]["service_day_count"] == 1
    assert manifest["invariants"]["rejected_routes_contribute_nothing"] is True
    assert result.benchmark_report_path.is_file()
    assert result.runtime_report_path.is_file()


def test_training_benchmark_uses_real_interval_labels_and_aft_fit(tmp_path: Path) -> None:
    feature_rows = [
        _feature_row(f"benchmark-{index:04d}", destination="stop") for index in range(1_001)
    ]
    feature_path = tmp_path / "features.parquet"
    pq.write_table(pa.Table.from_pylist(feature_rows, schema=FEATURE_SCHEMA), feature_path)
    feature_sha = hashlib.sha256(feature_path.read_bytes()).hexdigest()
    outcome_rows = [
        {
            "example_id": row["example_id"],
            "outcome_state": "LEFT_CENSORED",
            "lower_evidence_observation_id": row["anchor_observation_id"],
            "upper_evidence_observation_id": f"upper-{index}",
            "lower_bound_seconds": 0.0,
            "upper_bound_seconds": 60.0 + index % 10,
        }
        for index, row in enumerate(feature_rows)
    ]
    outcome_path = tmp_path / "outcomes.parquet"
    pq.write_table(pa.Table.from_pylist(outcome_rows, schema=OUTCOME_SCHEMA), outcome_path)
    outcome_sha = hashlib.sha256(outcome_path.read_bytes()).hexdigest()
    feature_entry = {
        "path": feature_path.relative_to(tmp_path).as_posix(),
        "sha256": feature_sha,
        "service_date": "2024-01-02",
        "split": DatasetSplit.TRAINING.value,
    }
    day_by_date = {
        "2024-01-02": {
            "outcomes": {
                "path": outcome_path.relative_to(tmp_path).as_posix(),
                "sealed": False,
                "sha256": outcome_sha,
            }
        }
    }
    sampled_rows, lower, upper, weights = _benchmark_sample([feature_entry], day_by_date, tmp_path)
    assert len(sampled_rows) == 1_001
    assert lower.min() == 0
    assert upper.min() >= 60
    assert weights.tolist() == [1.0] * 1_001
    transform = _fit_partitioned_transform([feature_entry], tmp_path)
    report = _run_benchmark(
        transform,
        [feature_entry],
        day_by_date,
        tmp_path,
        selected_example_count=1_001,
    )
    assert report["measured_sample_size"] == 1_001
    assert cast(int, report["matrix_nnz"]) > 0
    assert report["within_memory_budget"] is True
    assert report["within_temporary_storage_budget"] is True


def test_feature_materialization_reuses_one_cutoff_view_per_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2024, 1, 2, 12, tzinfo=UTC)
    observation = SimpleNamespace(observation_id="anchor", observation_utc=cutoff)
    episode = SimpleNamespace(episode_id="episode")
    scheduled_trip = SimpleNamespace(published_at_utc=cutoff)
    match = SimpleNamespace(
        episode=episode,
        reason=ScheduleMatchReason.EXACT,
        scheduled_trip=scheduled_trip,
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.build_trip_episodes",
        lambda _observations: SimpleNamespace(episodes=(episode,)),
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.match_episodes_to_schedule",
        lambda *_args, **_kwargs: SimpleNamespace(matches=(match,)),
    )
    view_calls = 0

    def make_view(*_args: object, **_kwargs: object) -> object:
        nonlocal view_calls
        view_calls += 1
        return object()

    monkeypatch.setattr(
        "arrive90_evaluation.model_population.ObservationCutoffView.from_episode", make_view
    )

    def make_feature(
        _match: object,
        _view: object,
        **kwargs: object,
    ) -> object:
        raw = _feature_row(
            f"feature-{kwargs['destination_offset']}",
            destination=str(kwargs["destination_stop_id"]),
        )
        raw["direction_id"] = 0
        values = tuple(
            sorted(
                ((name, raw[name]) for name in TRAVEL_TIME_V1_REGISTRY.specs),
                key=lambda item: item[0].encode(),
            )
        )
        return SimpleNamespace(
            feature_cutoff_utc=cutoff,
            source_observation_ids=("anchor",),
            values=values,
        )

    monkeypatch.setattr(
        "arrive90_evaluation.model_population.build_travel_time_feature_row", make_feature
    )
    selected = [
        {
            "example_id": f"example-{offset}",
            "episode_id": "episode",
            "anchor_observation_id": "anchor",
            "service_date": date(2024, 1, 2),
            "split": DatasetSplit.TRAINING.value,
            "base_weight": 0.5,
            "inclusion_probability": 1.0,
            "analysis_weight": 0.5,
            "destination_stop_id": f"stop-{offset}",
            "destination_stop_sequence": offset + 1,
            "destination_offset": offset,
            "scheduled_remaining_seconds": offset * 60,
        }
        for offset in (1, 2)
    ]
    rows = _feature_rows_for_day(
        selected,
        cast(Any, (observation,)),
        schedule_database=tmp_path / "schedule.db",
        schedule_database_sha256="s" * 64,
    )
    assert len(rows) == 2
    assert view_calls == 1
    assert {row["example_id"] for row in rows} == {"example-1", "example-2"}
    assert {row["direction_id"] for row in rows} == {"0"}


def test_blue_partition_reader_verifies_content_and_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partition = tmp_path / "blue.parquet"
    partition.write_bytes(b"blue")
    entry = {
        "path": partition.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(partition.read_bytes()).hexdigest(),
    }
    index = {(MODELED_ROUTE, date(2024, 1, 2)): entry}
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.read_normalized_partition", lambda _path: ()
    )
    assert (
        _blue_observations(date(2024, 1, 2), partition_index=index, normalized_root=tmp_path) == ()
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.read_normalized_partition",
        lambda _path: (SimpleNamespace(route_id="Red"),),
    )
    with pytest.raises(YearDatasetError, match="rejected route"):
        _blue_observations(date(2024, 1, 2), partition_index=index, normalized_root=tmp_path)


def test_transform_fit_rejects_empty_first_partition_and_reserved_category(
    tmp_path: Path,
) -> None:
    with pytest.raises(YearDatasetError, match="population is empty"):
        _fit_partitioned_transform([], tmp_path)

    empty_path = tmp_path / "empty.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=FEATURE_SCHEMA), empty_path)
    valid_path = tmp_path / "valid.parquet"
    pq.write_table(
        pa.Table.from_pylist([_feature_row("valid", destination="stop")], schema=FEATURE_SCHEMA),
        valid_path,
    )

    def entry(path: Path) -> dict[str, object]:
        return {
            "path": path.relative_to(tmp_path).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "split": DatasetSplit.TRAINING.value,
        }

    with pytest.raises(YearDatasetError, match="first selected training"):
        _fit_partitioned_transform([entry(empty_path), entry(valid_path)], tmp_path)

    reserved = _feature_row("reserved", destination="stop")
    reserved["route_id"] = MISSING_TOKEN
    reserved_path = tmp_path / "reserved.parquet"
    pq.write_table(pa.Table.from_pylist([reserved], schema=FEATURE_SCHEMA), reserved_path)
    with pytest.raises(YearDatasetError, match="reserved transform token"):
        _fit_partitioned_transform([entry(reserved_path)], tmp_path)


def test_benchmark_sample_rejects_sealed_training_and_insufficient_rows(
    tmp_path: Path,
) -> None:
    with pytest.raises(YearDatasetError, match="at least 1,000"):
        _benchmark_sample([{"split": DatasetSplit.MODEL_VALIDATION.value}], {}, tmp_path)
    with pytest.raises(YearDatasetError, match="training outcomes cannot be sealed"):
        _benchmark_sample(
            [
                {
                    "split": DatasetSplit.TRAINING.value,
                    "service_date": "2024-01-02",
                }
            ],
            {"2024-01-02": {"outcomes": {"sealed": True}}},
            tmp_path,
        )


def test_population_builder_fails_on_input_schedule_and_retention_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root = tmp_path / "dataset"
    unsampled_path = dataset_root / "manifests/unsampled.json"
    unsampled_path.parent.mkdir(parents=True)
    unsampled_path.write_text("{}", encoding="utf-8")
    unsampled: dict[str, object] = {
        "normalized_manifest_sha256": "a" * 64,
        "daily_partitions": [],
    }
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._active_unsampled_manifest",
        lambda _root: (unsampled_path, unsampled, "u" * 64),
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._normalized_manifest",
        lambda _root: (tmp_path / "normalized.json", {}, "b" * 64),
    )
    with pytest.raises(YearDatasetError, match="do not share"):
        build_model_population(
            normalized_root=tmp_path / "normalized",
            dataset_root=dataset_root,
            schedule_database=tmp_path / "missing.db",
            runtime_root=tmp_path / "runtime",
        )

    unsampled["normalized_manifest_sha256"] = "b" * 64
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._schedule_database_hash",
        lambda _manifest, _root: "s" * 64,
    )
    with pytest.raises(YearDatasetError, match="schedule database"):
        build_model_population(
            normalized_root=tmp_path / "normalized",
            dataset_root=dataset_root,
            schedule_database=tmp_path / "missing.db",
            runtime_root=tmp_path / "runtime",
        )

    schedule = tmp_path / "schedule.db"
    schedule.write_bytes(b"schedule")
    schedule_sha = hashlib.sha256(schedule.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "arrive90_evaluation.model_population._schedule_database_hash",
        lambda _manifest, _root: schedule_sha,
    )
    monkeypatch.setattr(
        "arrive90_evaluation.model_population.evaluate_blue_retention",
        lambda _manifest: RetentionResult(False, {"blue": False}, {}),
    )
    with pytest.raises(YearDatasetError, match="retention gate failed"):
        build_model_population(
            normalized_root=tmp_path / "normalized",
            dataset_root=dataset_root,
            schedule_database=schedule,
            runtime_root=tmp_path / "runtime",
        )
