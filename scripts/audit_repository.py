"""Audit tracked source, publication boundaries, generated data, and stale markers."""

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
    "docs/architecture.md",
    "docs/data-card.md",
    "docs/evaluation-report.md",
    "docs/limitations.md",
    "docs/model-card.md",
    "docs/operations.md",
    "docs/reproduction.md",
    "docs/security.md",
)
FORBIDDEN_TRACKED_PATTERNS = (
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"\.(?:db|key|parquet|pem|sqlite|sqlite3)$"),
    re.compile(
        r"^(?:data/(?:raw|normalized|models)|artifacts/(?:graphs|models|profiler|runtime))/"
    ),
)
STALE_MARKER = re.compile(r"\b(?:FIXME|PLACEHOLDER|TBD|TODO)\b", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
AUDITED_TEXT_PREFIXES = (
    "README.md",
    "benchmarks/",
    "configs/",
    "deployment/",
    "docs/",
    "packages/",
    "scripts/",
    "tools/",
)


def _git(*args: str) -> str:
    completed: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
        [GIT, *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def build_report() -> dict[str, Any]:
    tracked = tuple(sorted(_git("ls-files").splitlines(), key=str.encode))
    status_lines = tuple(
        line for line in _git("status", "--porcelain", "--untracked-files=all").splitlines() if line
    )
    missing_documents = [path for path in REQUIRED_DOCUMENTS if path not in tracked]
    forbidden_tracked = [
        path
        for path in tracked
        if any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS)
    ]
    stale_markers: list[dict[str, Any]] = []
    broken_local_links: list[dict[str, str]] = []
    for relative in tracked:
        if relative == "scripts/audit_repository.py":
            continue
        if not relative.endswith((".css", ".html", ".js", ".md", ".py", ".toml", ".yaml", ".yml")):
            continue
        if not relative.startswith(AUDITED_TEXT_PREFIXES):
            continue
        path = ROOT / relative
        content = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(content.splitlines(), start=1):
            if STALE_MARKER.search(line):
                stale_markers.append({"line": line_number, "path": relative})
        if relative.endswith(".md"):
            for match in MARKDOWN_LINK.finditer(content):
                target = match.group(1).strip().split("#", maxsplit=1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    broken_local_links.append({"path": relative, "target": target})
    security_path = ROOT / "artifacts/reports/qualification/milestone-9-security.json"
    licenses_path = ROOT / "artifacts/reports/qualification/licenses-v1.json"
    security = json.loads(security_path.read_text(encoding="utf-8"))
    licenses = json.loads(licenses_path.read_text(encoding="utf-8"))
    release_policy = (ROOT / "deployment/release-policy.yaml").read_text(encoding="utf-8")
    checks = {
        "all_required_public_documents_tracked": not missing_documents,
        "generated_or_sensitive_artifacts_not_tracked": not forbidden_tracked,
        "license_and_attribution_audit_passed": licenses.get("status") == "PASSED",
        "local_markdown_links_resolve": not broken_local_links,
        "publication_still_requires_user_authorization": (
            "publication_requires_explicit_user_authorization: true" in release_policy
        ),
        "repository_security_scan_passed": security.get("status") == "PASSED",
        "tracked_public_text_has_no_stale_markers": not stale_markers,
        "worktree_is_clean": not status_lines,
    }
    return {
        "checks": checks,
        "broken_local_links": broken_local_links,
        "commit": _git("rev-parse", "HEAD").strip(),
        "failing_checks": sorted(key for key, value in checks.items() if not value),
        "forbidden_tracked_paths": forbidden_tracked,
        "missing_documents": missing_documents,
        "stale_markers": stale_markers,
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "tracked_file_count": len(tracked),
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
