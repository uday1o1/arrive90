"""Hash-bound protocol freeze and immutable versioned report storage."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FrozenProtocol:
    acceptance_version: str
    frozen_at_utc: str
    query_manifest_hash: str
    candidate_manifest_hash: str
    model_bundle_hash: str
    calibration_hash: str
    support_manifest_hash: str
    eligibility_manifest_hash: str
    discovery_artifact_hash: str
    decision_policy_hash: str
    transfer_bundle_hash: str
    transfer_support_hash: str
    quantile_support_hash: str
    recovery_policy_hash: str
    secondary_hypothesis_hash: str
    evaluation_code_hash: str
    final_test_outcomes_opened: bool = False

    def __post_init__(self) -> None:
        hashes = (
            self.query_manifest_hash,
            self.candidate_manifest_hash,
            self.model_bundle_hash,
            self.calibration_hash,
            self.support_manifest_hash,
            self.eligibility_manifest_hash,
            self.discovery_artifact_hash,
            self.decision_policy_hash,
            self.transfer_bundle_hash,
            self.transfer_support_hash,
            self.quantile_support_hash,
            self.recovery_policy_hash,
            self.secondary_hypothesis_hash,
            self.evaluation_code_hash,
        )
        if any(
            len(value) != 64 or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes
        ):
            raise ValueError("every frozen manifest hash must be SHA-256")
        if self.final_test_outcomes_opened:
            raise ValueError("a new protocol cannot be frozen after final-test access")

    @property
    def protocol_hash(self) -> str:
        return canonical_hash(self.__dict__)


@dataclass(frozen=True)
class FinalTestAccess:
    protocol_hash: str
    opened_at_utc: str


def open_final_test(protocol: FrozenProtocol, *, opened_at_utc: str) -> FinalTestAccess:
    return FinalTestAccess(protocol.protocol_hash, opened_at_utc)


class ImmutableReportStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(
        self,
        *,
        acceptance_version: str,
        run_id: str,
        protocol_hash: str,
        report: dict[str, Any],
    ) -> Path:
        safe_component = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
        if (
            safe_component.fullmatch(acceptance_version) is None
            or safe_component.fullmatch(run_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", protocol_hash) is None
        ):
            raise ValueError("immutable report identity is invalid")
        required = {"censoring_bounds", "negative_results", "uncertainty", "availability"}
        if required - report.keys():
            raise ValueError("evaluation report omits required evidence sections")
        destination = self.root / acceptance_version / f"{run_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {"protocol_hash": protocol_hash, **report}
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        return destination


@dataclass(frozen=True)
class FrozenCellResult:
    cell_id: str
    pretest_eligible: bool
    final_test_passed: bool | None


def frozen_policy_passes(cells: tuple[FrozenCellResult, ...]) -> bool:
    if len({cell.cell_id for cell in cells}) != len(cells):
        raise ValueError("frozen cell results must be unique")
    return all(not cell.pretest_eligible or cell.final_test_passed is True for cell in cells)
