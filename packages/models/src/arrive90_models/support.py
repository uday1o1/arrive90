"""Pre-fit support inventory and fail-closed lookup surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupportCellEvidence:
    cell_id: str
    candidate_outcome_count: int
    base_query_count: int
    service_day_count: int
    eligible: bool
    reasons: tuple[str, ...]


def deadline_support_cell(
    cell_id: str,
    *,
    candidate_outcome_count: int,
    base_query_count: int,
    service_day_count: int,
) -> SupportCellEvidence:
    reasons: list[str] = []
    if candidate_outcome_count < 1_000:
        reasons.append("CANDIDATE_OUTCOME_COUNT_BELOW_1000")
    if base_query_count < 500:
        reasons.append("BASE_QUERY_COUNT_BELOW_500")
    if service_day_count < 30:
        reasons.append("SERVICE_DAY_COUNT_BELOW_30")
    return SupportCellEvidence(
        cell_id,
        candidate_outcome_count,
        base_query_count,
        service_day_count,
        not reasons,
        tuple(reasons),
    )


class SupportManifest:
    def __init__(self, cells: tuple[SupportCellEvidence, ...]) -> None:
        if len({cell.cell_id for cell in cells}) != len(cells):
            raise ValueError("support manifest contains duplicate cells")
        self.cells = {cell.cell_id: cell for cell in cells}

    def is_eligible(self, cell_id: str) -> bool:
        cell = self.cells.get(cell_id)
        return cell is not None and cell.eligible
