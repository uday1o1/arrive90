"""Score one frozen eight-participant comprehension cohort from JSON."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from arrive90_evaluation.comprehension import ParticipantResponse, score_comprehension


def build_report(source: Path, *, prior: Path | None) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    responses = tuple(ParticipantResponse(**item) for item in raw["responses"])
    prior_ids: frozenset[str] = frozenset()
    if prior is not None:
        prior_report = json.loads(prior.read_text(encoding="utf-8"))
        prior_ids = frozenset(prior_report["participant_ids"])
    result = score_comprehension(responses, prior_participant_ids=prior_ids)
    return {
        "participant_ids": [response.participant_id for response in responses],
        "protocol_version": "COMPREHENSION_V1",
        "result": asdict(result),
        "status": "PASSED" if result.passed else "FAILED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prior-cohort-report", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("comprehension reports are create-only")
    report = build_report(args.input, prior=args.prior_cohort_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
