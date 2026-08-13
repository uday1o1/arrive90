"""Route-policy recall accounting against the static audit enumerator."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class RecallCase:
    query_id: str
    expected_policy_keys: frozenset[str]
    produced_policy_keys: frozenset[str]
    expected_fastest_policy_key: str
    slices: tuple[str, ...]


@dataclass(frozen=True)
class RecallMetric:
    recovered: int
    expected: int
    rate: float


@dataclass(frozen=True)
class RecallReport:
    overall: RecallMetric
    by_slice: dict[str, RecallMetric]
    fastest_recovered_for_every_query: bool


def evaluate_recall(cases: Iterable[RecallCase]) -> RecallReport:
    case_list = list(cases)
    recovered = sum(
        len(case.expected_policy_keys & case.produced_policy_keys) for case in case_list
    )
    expected = sum(len(case.expected_policy_keys) for case in case_list)
    if expected == 0:
        raise ValueError("recall corpus has no expected policies")
    by_slice_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in case_list:
        case_recovered = len(case.expected_policy_keys & case.produced_policy_keys)
        for slice_name in case.slices:
            by_slice_counts[slice_name][0] += case_recovered
            by_slice_counts[slice_name][1] += len(case.expected_policy_keys)
    metrics = {
        name: RecallMetric(values[0], values[1], values[0] / values[1])
        for name, values in sorted(by_slice_counts.items())
    }
    return RecallReport(
        overall=RecallMetric(recovered, expected, recovered / expected),
        by_slice=metrics,
        fastest_recovered_for_every_query=all(
            case.expected_fastest_policy_key in case.produced_policy_keys for case in case_list
        ),
    )
