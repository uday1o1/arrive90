from arrive90_evaluation.comprehension import ParticipantResponse, score_comprehension


def _responses(*, incorrect: int = 1) -> tuple[ParticipantResponse, ...]:
    return tuple(
        ParticipantResponse(
            f"participant-{index}",
            True,
            False,
            index >= incorrect,
            index >= incorrect,
        )
        for index in range(8)
    )


def test_seven_of_eight_independent_participants_passes_exact_gate() -> None:
    result = score_comprehension(_responses())
    assert result.passed
    assert result.cohort_valid
    assert result.both_checks_correct_count == 7


def test_invalid_or_underperforming_cohort_fails_without_reinterpretation() -> None:
    result = score_comprehension(_responses(incorrect=2))
    assert not result.passed
    assert result.failing_checks == ("FEWER_THAN_SEVEN_PASS_BOTH_CHECKS",)
    prior = frozenset({"participant-7"})
    reused = score_comprehension(_responses(), prior_participant_ids=prior)
    assert not reused.passed
    assert "REVISED_COHORT_REUSES_PRIOR_PARTICIPANT" in reused.failing_checks
    duplicate = (*_responses()[:7], _responses()[0])
    invalid = score_comprehension(duplicate)
    assert not invalid.cohort_valid
    assert "DUPLICATE_PARTICIPANT_IDENTIFIER" in invalid.failing_checks
