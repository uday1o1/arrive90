"""Write the fail-closed Milestone 7 rider-product gate report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _combined_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix().encode()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_report() -> dict[str, Any]:
    acceptance = ROOT / "configs/acceptance/v1.yaml"
    milestone_six_path = ROOT / "artifacts/reports/gates/milestone-6.json"
    browser_path = ROOT / "artifacts/reports/qualification/milestone-7-browser.json"
    milestone_six = json.loads(milestone_six_path.read_text(encoding="utf-8"))
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    html_path = ROOT / "packages/service/src/arrive90_service/web/index.html"
    html = html_path.read_text(encoding="utf-8")
    cli = (ROOT / "packages/service/src/arrive90_service/cli.py").read_text(encoding="utf-8")
    local_checks = {
        "accessible_no_map_and_keyboard_browser_path_passed": browser.get("status") == "PASSED",
        "browser_direct_transfer_recovery_and_failure_paths_passed": browser.get("status")
        == "PASSED",
        "methodology_attribution_competitor_and_limitations_views_present": all(
            marker in html
            for marker in ("Methodology", "Limitations", "Competitor matrix", "MassDOT")
        ),
        "milestone_6_evidence_link_present": "/milestone-6-card.json" in html,
        "nonselected_model_outputs_visibly_unavailable": (
            "Probability unavailable: this comparator output has not been validated."
            in (ROOT / "packages/service/src/arrive90_service/web/app.js").read_text(
                encoding="utf-8"
            )
        ),
        "prospective_calibration_labeled_pending": "Prospective live calibration is pending"
        in html,
        "synthetic_interface_demonstration_present": (
            ROOT / "artifacts/demos/milestone-7-synthetic-ui.png"
        ).is_file(),
        "trip_session_and_authenticated_sse_ui_implemented": "refreshEvents"
        in (ROOT / "packages/service/src/arrive90_service/web/app.js").read_text(encoding="utf-8"),
        "web_application_remains_loopback_only": "non-loopback startup is disabled" in cli,
    }
    acceptance_checks = {
        "eight_participant_comprehension_gate_passed": False,
        "immutable_historical_replay_demonstration_present": False,
        "milestone_6_accepted": milestone_six.get("status") == "PASSED",
        "offline_claims_authorized_by_accepted_milestone_6_artifact": False,
    }
    checks = {**local_checks, **acceptance_checks}
    failing = sorted(key for key, passed in checks.items() if not passed)
    return {
        "acceptance_version": "v1",
        "acceptance_version_hash": _digest(acceptance),
        "checks": checks,
        "command": (
            "make check-all && make qualify-milestone7 && make milestone7-evidence "
            "&& make gate MILESTONE=7"
        ),
        "failing_checks": failing,
        "input_manifest_hashes": {
            "acceptance_charter": _digest(acceptance),
            "browser_qualification": _digest(browser_path),
            "comprehension_protocol": _digest(ROOT / "docs/comprehension-protocol.md"),
            "frontend": _combined_digest(
                list((ROOT / "packages/service/src/arrive90_service/web").glob("*"))
            ),
            "milestone_6_report": _digest(milestone_six_path),
            "synthetic_demonstration": _digest(
                ROOT / "artifacts/demos/milestone-7-synthetic-ui.png"
            ),
        },
        "milestone": 7,
        "missing_prerequisite": (
            "An accepted Milestone 6 empirical or model-free result, a retained historical replay, "
            "and a passing independent eight-participant comprehension cohort are required."
        ),
        "resume_command": (
            "Resume the source gate, complete Milestone 6, then run the protocol in "
            "docs/comprehension-protocol.md."
        ),
        "status": "PASSED" if not failing else "INSUFFICIENT_EVIDENCE",
    }


def main() -> int:
    output = ROOT / "artifacts/reports/gates/milestone-7.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
