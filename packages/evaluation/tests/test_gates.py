import pytest
from arrive90_evaluation.freeze import FrozenCellResult
from arrive90_evaluation.gates import (
    CellEvidence,
    CellKind,
    PerformanceEvidence,
    PrimaryGateEvidence,
    evaluate_cell_gate,
    evaluate_primary_gate,
)


@pytest.mark.parametrize(
    ("kind", "decisions", "queries", "days", "uncertainty"),
    [
        (CellKind.DEADLINE_BAND, 500, 250, 50, 0.05),
        (CellKind.DEADLINE_SLICE, 250, 125, 30, 0.08),
        (CellKind.PROSPECTIVE_095, 800, 400, 56, 0.03),
        (CellKind.TRANSFER_DECILE, 500, 250, 40, 0.08),
        (CellKind.TRANSFER_STATION, 800, 400, 50, 0.08),
        (CellKind.SELECTED_TRIGGER_DECILE, 300, 150, 30, 0.08),
        (CellKind.QUANTILE_LEVEL, 1_000, 500, 50, 0.08),
    ],
)
def test_every_frozen_cell_gate_passes_at_its_exact_boundary(
    kind: CellKind,
    decisions: int,
    queries: int,
    days: int,
    uncertainty: float,
) -> None:
    result = evaluate_cell_gate(CellEvidence("cell", kind, decisions, queries, days, uncertainty))
    assert result.eligible
    assert result.reasons == ()


def test_cell_gate_reports_every_failure_without_posttest_suppression() -> None:
    result = evaluate_cell_gate(CellEvidence("cell", CellKind.DEADLINE_BAND, 0, 0, 0, 1.0))
    assert not result.eligible
    assert result.reasons == (
        "DECISION_COUNT_BELOW_MINIMUM",
        "BASE_QUERY_COUNT_BELOW_MINIMUM",
        "SERVICE_DAY_COUNT_BELOW_MINIMUM",
        "UNCERTAINTY_UPPER_EXCEEDS_MAXIMUM",
    )
    with pytest.raises(ValueError, match="negative"):
        CellEvidence("bad", CellKind.DEADLINE_BAND, -1, 0, 0, 0)


def _primary(**changes: object) -> PrimaryGateEvidence:
    values: dict[str, object] = {
        "empirical_primary_evidence": True,
        "primary_difference_lower_ci": 0.001,
        "pair_resolution_rate": 0.90,
        "slice_pair_resolution_rates": (("red-peak", 0.80),),
        "mean_added_planned_time_seconds": 600,
        "maximum_added_planned_time_seconds": 1_200,
        "added_time_population_available": True,
        "performance": PerformanceEvidence(99.999, 99.999, 999.999),
        "frozen_cells": (FrozenCellResult("band", True, True),),
    }
    values.update(changes)
    return PrimaryGateEvidence(**values)  # type: ignore[arg-type]


def test_primary_gate_passes_exact_nonstrict_boundaries_but_requires_positive_improvement() -> None:
    assert evaluate_primary_gate(_primary()).passed
    result = evaluate_primary_gate(_primary(primary_difference_lower_ci=0))
    assert not result.passed
    assert result.failing_checks == ("PRIMARY_WORST_CASE_BOUND_LOWER_CI_NOT_ABOVE_ZERO",)


def test_primary_gate_reports_all_failures_and_never_suppresses_a_frozen_cell() -> None:
    result = evaluate_primary_gate(
        _primary(
            empirical_primary_evidence=False,
            primary_difference_lower_ci=-0.1,
            pair_resolution_rate=0.89,
            slice_pair_resolution_rates=(("red-peak", 0.79),),
            mean_added_planned_time_seconds=601,
            maximum_added_planned_time_seconds=1_201,
            added_time_population_available=False,
            performance=PerformanceEvidence(100, 100, 1_000),
            frozen_cells=(FrozenCellResult("band", True, False),),
        )
    )
    assert not result.passed
    assert len(result.failing_checks) == 11
    assert "PRETEST_ELIGIBLE_OUTPUT_SUPPORT_CELL_FAILED" in result.failing_checks
    with pytest.raises(ValueError, match="unique"):
        _primary(slice_pair_resolution_rates=(("same", 0.9), ("same", 0.9)))
