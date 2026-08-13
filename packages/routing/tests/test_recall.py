from __future__ import annotations

import pytest
from arrive90_routing.recall import RecallCase, evaluate_recall


def test_recall_reports_overall_slices_and_fastest_recovery() -> None:
    report = evaluate_recall(
        (
            RecallCase("a", frozenset({"fast", "other"}), frozenset({"fast"}), "fast", ("Red",)),
            RecallCase("b", frozenset({"x"}), frozenset(), "x", ("Orange",)),
        )
    )
    assert report.overall.recovered == 1
    assert report.overall.rate == pytest.approx(1 / 3)
    assert report.by_slice["Red"].rate == 0.5
    assert report.by_slice["Orange"].rate == 0.0
    assert not report.fastest_recovered_for_every_query
    with pytest.raises(ValueError, match="no expected"):
        evaluate_recall(())
