import pytest
from arrive90_evaluation.hypotheses import (
    Hypothesis,
    ParetoPoint,
    holm_correction,
    pareto_frontier,
)


def test_holm_correction_uses_declared_order_for_ties_and_step_down_stopping() -> None:
    hypotheses = (
        Hypothesis("secondary-b", 0.01, 1),
        Hypothesis("secondary-a", 0.01, 0),
        Hypothesis("secondary-c", 0.04, 2),
    )
    results = holm_correction(hypotheses)
    by_id = {result.hypothesis_id: result for result in results}
    assert by_id["secondary-a"].testing_rank == 1
    assert by_id["secondary-b"].testing_rank == 2
    assert by_id["secondary-a"].adjusted_p_value == 0.03
    assert by_id["secondary-b"].adjusted_p_value == 0.03
    assert by_id["secondary-c"].adjusted_p_value == 0.04
    assert all(result.rejected for result in results)


def test_holm_and_hypothesis_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="invalid"):
        Hypothesis("bad", 1.1, 0)
    with pytest.raises(ValueError, match="requires"):
        holm_correction(())
    duplicate = (Hypothesis("same", 0.1, 0), Hypothesis("same", 0.2, 1))
    with pytest.raises(ValueError, match="unique"):
        holm_correction(duplicate)


def test_reliability_time_pareto_frontier_removes_dominated_points() -> None:
    points = (
        ParetoPoint("fast", 0.7, 0.8, 0),
        ParetoPoint("dominated", 0.65, 0.9, 60),
        ParetoPoint("confirmatory", 0.85, 0.9, 300, True),
        ParetoPoint("safe", 0.9, 0.95, 600),
    )
    frontier = pareto_frontier(points)
    assert [point.policy_id for point in frontier] == ["fast", "confirmatory", "safe"]
    with pytest.raises(ValueError, match="nonempty"):
        pareto_frontier(())
