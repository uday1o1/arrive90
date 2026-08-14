import json
from pathlib import Path

import pytest
from arrive90_evaluation.freeze import (
    FrozenCellResult,
    FrozenProtocol,
    ImmutableReportStore,
    canonical_hash,
    frozen_policy_passes,
    open_final_test,
)

HASH = "a" * 64


def _protocol(**changes: object) -> FrozenProtocol:
    values: dict[str, object] = {
        "acceptance_version": "v1",
        "frozen_at_utc": "2025-01-01T00:00:00Z",
        "query_manifest_hash": HASH,
        "candidate_manifest_hash": HASH,
        "model_bundle_hash": HASH,
        "calibration_hash": HASH,
        "support_manifest_hash": HASH,
        "eligibility_manifest_hash": HASH,
        "discovery_artifact_hash": HASH,
        "decision_policy_hash": HASH,
        "transfer_bundle_hash": HASH,
        "transfer_support_hash": HASH,
        "quantile_support_hash": HASH,
        "recovery_policy_hash": HASH,
        "secondary_hypothesis_hash": HASH,
        "evaluation_code_hash": HASH,
    }
    values.update(changes)
    return FrozenProtocol(**values)  # type: ignore[arg-type]


def test_protocol_hash_precedes_final_test_access() -> None:
    protocol = _protocol()
    assert protocol.protocol_hash == canonical_hash(protocol.__dict__)
    access = open_final_test(protocol, opened_at_utc="2025-02-01T00:00:00Z")
    assert access.protocol_hash == protocol.protocol_hash
    with pytest.raises(ValueError, match="SHA-256"):
        _protocol(query_manifest_hash="missing")
    with pytest.raises(ValueError, match="SHA-256"):
        _protocol(query_manifest_hash="G" * 64)
    with pytest.raises(ValueError, match="after final-test"):
        _protocol(final_test_outcomes_opened=True)


def test_versioned_reports_are_create_only_and_require_negative_evidence(tmp_path: Path) -> None:
    store = ImmutableReportStore(tmp_path)
    report = {
        "availability": {"rate": 0.9},
        "censoring_bounds": {"lower": -0.1, "upper": 0.2},
        "negative_results": ["primary lower bound did not pass"],
        "uncertainty": {"method": "service-day bootstrap"},
    }
    path = store.write(
        acceptance_version="v1",
        run_id="run-1",
        protocol_hash=HASH,
        report=report,
    )
    assert json.loads(path.read_text())["negative_results"]
    with pytest.raises(FileExistsError):
        store.write(
            acceptance_version="v1",
            run_id="run-1",
            protocol_hash=HASH,
            report=report,
        )
    with pytest.raises(ValueError, match="omits"):
        store.write(
            acceptance_version="v1",
            run_id="run-2",
            protocol_hash=HASH,
            report={"availability": {}},
        )
    with pytest.raises(ValueError, match="identity"):
        store.write(
            acceptance_version="../escape",
            run_id="run-3",
            protocol_hash=HASH,
            report=report,
        )


def test_pretest_eligible_cell_failure_cannot_be_suppressed_after_test() -> None:
    assert frozen_policy_passes(
        (
            FrozenCellResult("eligible", True, True),
            FrozenCellResult("ineligible", False, None),
        )
    )
    assert not frozen_policy_passes((FrozenCellResult("failed", True, False),))
    assert not frozen_policy_passes((FrozenCellResult("missing", True, None),))
    with pytest.raises(ValueError, match="unique"):
        frozen_policy_passes(
            (FrozenCellResult("same", True, True), FrozenCellResult("same", False, None))
        )
