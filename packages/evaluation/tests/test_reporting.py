from decimal import Decimal

import pytest
from arrive90_evaluation.freeze import FrozenCellResult, FrozenProtocol, open_final_test
from arrive90_evaluation.gates import PerformanceEvidence
from arrive90_evaluation.hypotheses import Hypothesis
from arrive90_evaluation.metrics import PolicyPairRow, PredictionRow, QuantileRow
from arrive90_evaluation.reporting import EvaluationInputs, build_evaluation_report

HASH = "a" * 64


def _protocol() -> FrozenProtocol:
    return FrozenProtocol(
        "v1",
        "2025-01-01T00:00:00Z",
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
        HASH,
    )


def _inputs(**changes: object) -> EvaluationInputs:
    protocol = _protocol()
    values: dict[str, object] = {
        "protocol": protocol,
        "access": open_final_test(protocol, opened_at_utc="2025-02-01T00:00:00Z"),
        "prediction_rows": (
            PredictionRow("d1", "q1", "day-1", 1, Decimal("0.9"), 0.9, True),
            PredictionRow("d2", "q2", "day-2", 1, Decimal("0.9"), 0.9, True),
        ),
        "primary_policy_rows": (
            PolicyPairRow("v1", "q1", "day-1", 1, True, False, 60, ("ordinary",)),
            PolicyPairRow("v2", "q2", "day-2", 1, True, False, 60, ("disruption",)),
        ),
        "recovery_policy_rows": (
            PolicyPairRow("r1", "q1", "day-1", 1, True, False, 30),
            PolicyPairRow("r2", "q2", "day-2", 1, True, True, 30),
        ),
        "quantile_rows": (
            QuantileRow("day-1", 1, 0.9, 10, 8, 9),
            QuantileRow("day-2", 1, 0.9, 10, 9, 11),
        ),
        "final_cell_results": (FrozenCellResult("band", True, True),),
        "secondary_hypotheses": (Hypothesis("recovery", 0.02, 0),),
        "performance": PerformanceEvidence(1, 1, 2),
        "empirical_primary_evidence": False,
        "model_free_bundle_passed": False,
        "bootstrap_seed": 17,
    }
    values.update(changes)
    return EvaluationInputs(**values)  # type: ignore[arg-type]


def test_report_keeps_synthetic_evidence_out_of_a_passing_release_claim() -> None:
    report = build_evaluation_report(_inputs())
    assert report["evidence_kind"] == "SYNTHETIC_MECHANICS_ONLY"
    assert report["gate"]["passed"] is False
    assert report["release_mode"] == "HISTORICAL_EXPLORER"
    assert report["uncertainty"]["replicates"] == 2_000
    assert report["censoring_bounds"] == {
        "primary_difference_lower": 1.0,
        "primary_difference_upper": 1.0,
    }
    assert {point["policy_id"] for point in report["pareto_frontier"]} == {
        "ARRIVE90_0_90_CAP_20",
        "STATIC_FASTEST",
    }
    assert report["recovery"]["recovery_model_outputs"] == "ALWAYS_NULL"


def test_report_rejects_access_from_another_protocol() -> None:
    other = _protocol()
    object.__setattr__(other, "query_manifest_hash", "b" * 64)
    with pytest.raises(ValueError, match="does not match"):
        build_evaluation_report(_inputs(access=open_final_test(other, opened_at_utc="later")))
