from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_public_charts_are_deterministic_and_bound_to_final_results() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/build_docs_assets.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    outputs = sorted((ROOT / "docs/assets").glob("*.svg"))
    assert len(outputs) == 3
    combined = "\n".join(path.read_text(encoding="utf-8") for path in outputs)
    assert "1.647" in combined
    assert "30.41" in combined
    assert "0.026" in combined
