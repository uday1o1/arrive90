"""Audit the active portfolio tree for source, evidence, claims, and hygiene."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GIT: str = shutil.which("git") or ""
if not GIT:
    raise RuntimeError("git is required for the repository audit")

REQUIRED_DOCUMENTS = (
    "README.md",
    "DATA_LICENSE.md",
    "docs/acceptance-charter.md",
    "docs/architecture.md",
    "docs/data-card.md",
    "docs/evaluation-report.md",
    "docs/ingestion.md",
    "docs/limitations.md",
    "docs/methodology.md",
    "docs/model-card.md",
    "docs/replay-demonstration.md",
    "docs/reproduction.md",
    "docs/source-feasibility.md",
    "docs/temporal-semantics.md",
)
REQUIRED_PUBLIC_ARTIFACTS = (
    "artifacts/demos/replay-explorer.png",
    "artifacts/demos/replay-explorer-walkthrough.webm",
    "artifacts/reports/claims/travel-time-v1.2.json",
    "artifacts/reports/final/travel-time-v1.2.json",
    "docs/assets/calibration-ece.svg",
    "docs/assets/model-comparison.svg",
    "docs/assets/point-comparison.svg",
)
FORBIDDEN_TRACKED_PATTERNS = (
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"\.(?:db|key|parquet|pem|sqlite|sqlite3)$"),
    re.compile(r"^(?:data/(?:raw|normalized|datasets|models)|artifacts/runtime)/"),
)
STALE_MARKER = re.compile(r"\b(?:FIXME|PLACEHOLDER|TBD|TODO)\b", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SECRET_PATTERNS = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[ps]_[A-Za-z0-9]{30,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "assigned_secret",
        re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
    ),
)
OLD_PUBLIC_CLAIMS = (
    "MBTA subway journey planner",
    "trip-start capability",
    "prospective live calibration",
    "schedule-only abstention",
    "recovery recommendation",
    "one-transfer candidate",
    "boarding probability",
)
RETIRED_PATH_PREFIXES = (
    "deployment/",
    "packages/decision/",
    "packages/routing/",
)
TEXT_SUFFIXES = (".css", ".html", ".js", ".json", ".md", ".py", ".toml", ".yaml", ".yml")
SOURCE_SUFFIXES = (".css", ".html", ".js", ".md", ".py", ".svg", ".toml", ".yaml", ".yml")


def _git(*args: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed Git executable and repository arguments
        [GIT, *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _public_text(tracked: tuple[str, ...]) -> str:
    public = ["README.md", "DATA_LICENSE.md", *REQUIRED_DOCUMENTS[2:]]
    return "\n".join(
        (ROOT / relative).read_text(encoding="utf-8") for relative in public if relative in tracked
    )


def build_report() -> dict[str, Any]:
    tracked = tuple(sorted(_git("ls-files").splitlines(), key=str.encode))
    status_lines = tuple(
        line for line in _git("status", "--porcelain", "--untracked-files=all").splitlines() if line
    )
    missing_documents = [path for path in REQUIRED_DOCUMENTS if path not in tracked]
    missing_artifacts = [path for path in REQUIRED_PUBLIC_ARTIFACTS if path not in tracked]
    forbidden_tracked = [
        path
        for path in tracked
        if any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS)
    ]
    retired_paths = [
        path for path in tracked if any(path.startswith(prefix) for prefix in RETIRED_PATH_PREFIXES)
    ]
    stale_markers: list[dict[str, Any]] = []
    secret_findings: list[dict[str, Any]] = []
    broken_local_links: list[dict[str, str]] = []
    for relative in tracked:
        if relative in {"BUILD_PLAN.md", "scripts/audit_repository.py"} or not relative.endswith(
            TEXT_SUFFIXES
        ):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(content.splitlines(), start=1):
            if STALE_MARKER.search(line):
                stale_markers.append({"line": line_number, "path": relative})
            for name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    secret_findings.append({"kind": name, "line": line_number, "path": relative})
        if relative.endswith(".md"):
            for match in MARKDOWN_LINK.finditer(content):
                target = match.group(1).strip().strip("<>").split("#", maxsplit=1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                if not (path.parent / target).resolve().exists():
                    broken_local_links.append({"path": relative, "target": target})
    untracked_source = [
        line[3:]
        for line in status_lines
        if line.startswith("?? ") and line[3:].endswith(SOURCE_SUFFIXES)
    ]
    public_text = _public_text(tracked)
    old_claims = [
        claim for claim in OLD_PUBLIC_CLAIMS if claim.casefold() in public_text.casefold()
    ]
    attribution = (ROOT / "DATA_LICENSE.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    checks = {
        "all_required_public_artifacts_tracked": not missing_artifacts,
        "all_required_public_documents_tracked": not missing_documents,
        "bulk_and_generated_roots_are_ignored": all(
            marker in gitignore
            for marker in (
                "data/raw/",
                "data/normalized/",
                "data/datasets/",
                "data/models/",
                "artifacts/runtime/",
            )
        ),
        "generated_or_sensitive_artifacts_not_tracked": not forbidden_tracked,
        "local_markdown_links_resolve": not broken_local_links,
        "no_accidental_secret_pattern_in_tracked_text": not secret_findings,
        "no_retired_package_or_deployment_path": not retired_paths,
        "no_stale_public_scope_claim": not old_claims,
        "source_attribution_and_noncommercial_notice_present": all(
            marker in attribution
            for marker in (
                "Jacobs Urban Tech Hub at Cornell Tech",
                "CC BY-NC 4.0",
                "MassDOT",
                "MBTA",
                "noncommercial",
                "not affiliated",
            )
        ),
        "tracked_public_text_has_no_stale_markers": not stale_markers,
        "untracked_required_source_is_absent": not untracked_source,
        "worktree_is_clean": not status_lines,
    }
    return {
        "acceptance_version": "travel-time-v1.2",
        "broken_local_links": broken_local_links,
        "checks": checks,
        "commit": _git("rev-parse", "HEAD").strip(),
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "forbidden_tracked_paths": forbidden_tracked,
        "missing_artifacts": missing_artifacts,
        "missing_documents": missing_documents,
        "old_public_claims": old_claims,
        "retired_paths": retired_paths,
        "secret_findings": secret_findings,
        "stale_markers": stale_markers,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "tracked_file_count": len(tracked),
        "untracked_source": untracked_source,
        "version": "repository-audit-v1.2",
        "worktree_status": list(status_lines),
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--output", type=Path, required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
