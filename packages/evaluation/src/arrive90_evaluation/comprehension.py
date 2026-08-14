"""Predeclared scoring for the eight-participant comprehension gate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParticipantResponse:
    participant_id: str
    independent: bool
    saw_earlier_evaluated_interface: bool
    estimate_not_guarantee_correct: bool
    target_not_met_correct: bool

    def __post_init__(self) -> None:
        if not self.participant_id or len(self.participant_id) > 128:
            raise ValueError("participant identifier is invalid")


@dataclass(frozen=True)
class ComprehensionResult:
    passed: bool
    cohort_valid: bool
    participant_count: int
    both_checks_correct_count: int
    failing_checks: tuple[str, ...]


def score_comprehension(
    responses: tuple[ParticipantResponse, ...],
    *,
    prior_participant_ids: frozenset[str] = frozenset(),
) -> ComprehensionResult:
    failures: list[str] = []
    identifiers = tuple(response.participant_id for response in responses)
    if len(responses) != 8:
        failures.append("PARTICIPANT_COUNT_NOT_EXACTLY_EIGHT")
    if len(set(identifiers)) != len(identifiers):
        failures.append("DUPLICATE_PARTICIPANT_IDENTIFIER")
    if any(not response.independent for response in responses):
        failures.append("PARTICIPANT_NOT_INDEPENDENT")
    if any(response.saw_earlier_evaluated_interface for response in responses):
        failures.append("PARTICIPANT_SAW_EARLIER_EVALUATED_INTERFACE")
    if prior_participant_ids & set(identifiers):
        failures.append("REVISED_COHORT_REUSES_PRIOR_PARTICIPANT")
    correct = sum(
        response.estimate_not_guarantee_correct and response.target_not_met_correct
        for response in responses
    )
    if correct < 7:
        failures.append("FEWER_THAN_SEVEN_PASS_BOTH_CHECKS")
    cohort_failures = {
        "PARTICIPANT_COUNT_NOT_EXACTLY_EIGHT",
        "DUPLICATE_PARTICIPANT_IDENTIFIER",
        "PARTICIPANT_NOT_INDEPENDENT",
        "PARTICIPANT_SAW_EARLIER_EVALUATED_INTERFACE",
        "REVISED_COHORT_REUSES_PRIOR_PARTICIPANT",
    }
    cohort_valid = not cohort_failures.intersection(failures)
    return ComprehensionResult(not failures, cohort_valid, len(responses), correct, tuple(failures))
