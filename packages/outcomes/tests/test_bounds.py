from __future__ import annotations

import pytest
from arrive90_outcomes.bounds import (
    WeightedBinaryOutcome,
    WeightedPolicyPair,
    binary_success_bounds,
    paired_difference_bounds,
)


def test_binary_success_bounds_keep_unresolved_mass_in_denominator() -> None:
    bounds = binary_success_bounds(
        (
            WeightedBinaryOutcome(True, 1),
            WeightedBinaryOutcome(False, 1),
            WeightedBinaryOutcome(None, 2),
        )
    )
    assert bounds.lower == 0.25
    assert bounds.upper == 0.75
    assert bounds.resolved_weight == 2
    assert bounds.total_weight == 4


def test_paired_difference_bounds_cover_every_resolution_combination() -> None:
    bounds = paired_difference_bounds(
        (
            WeightedPolicyPair(True, False, 1),
            WeightedPolicyPair(None, True, 1),
            WeightedPolicyPair(False, None, 1),
            WeightedPolicyPair(None, None, 1),
        )
    )
    assert bounds.lower == pytest.approx(-0.5)
    assert bounds.upper == pytest.approx(0.5)
    assert bounds.resolved_weight == 1


def test_bounds_reject_empty_or_nonpositive_weights() -> None:
    with pytest.raises(ValueError, match="positive"):
        binary_success_bounds(())
    with pytest.raises(ValueError, match="positive"):
        paired_difference_bounds((WeightedPolicyPair(True, True, 0),))
