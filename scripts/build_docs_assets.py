"""Render deterministic public result charts from the immutable final report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "artifacts/reports/final/travel-time-v1.2.json"
ASSET_ROOT = ROOT / "docs/assets"


def _text(x: float, y: float, value: str, *, css: str = "label") -> str:
    return f'<text class="{css}" x="{x:.1f}" y="{y:.1f}">{escape(value)}</text>'


def _document(title: str, description: str, body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" role="img" '
        'viewBox="0 0 960 520">\n'
        f"  <title>{escape(title)}</title>\n"
        f"  <desc>{escape(description)}</desc>\n"
        "  <style>\n"
        "    .title { fill: #102a43; font: 700 26px system-ui, sans-serif; }\n"
        "    .subtitle { fill: #486581; font: 15px system-ui, sans-serif; }\n"
        "    .label { fill: #243b53; font: 15px system-ui, sans-serif; }\n"
        "    .value { fill: #102a43; font: 700 15px ui-monospace, monospace; }\n"
        "    .axis { stroke: #9fb3c8; stroke-width: 1; }\n"
        "    .grid { stroke: #d9e2ec; stroke-width: 1; }\n"
        "  </style>\n"
        '  <rect width="960" height="520" fill="#f7f9fc" rx="18"/>\n'
        f"{body}"
        "</svg>\n"
    )


def _model_comparison(report: dict[str, Any]) -> str:
    models = report["models"]
    identifiers = (
        "FULL-normal-scale-0p5",
        "NO_POSITION_OBSERVATION-normal",
        "NO_PREFIX_HISTORY-normal",
        "SCHEDULE_CALENDAR-normal",
        "INTERCEPT_ONLY-normal",
    )
    labels = {
        "FULL-normal-scale-0p5": "Promoted full model",
        "NO_POSITION_OBSERVATION-normal": "Without position features",
        "NO_PREFIX_HISTORY-normal": "Without prefix history",
        "SCHEDULE_CALENDAR-normal": "Schedule and calendar baseline",
        "INTERCEPT_ONLY-normal": "Intercept-only baseline",
    }
    values = [float(models[item]["interval_negative_log_likelihood"]) for item in identifiers]
    low = 1.5
    high = 3.1
    left = 330.0
    width = 540.0
    parts = [
        _text(48, 54, "Held-out interval likelihood", css="title"),
        _text(48, 82, "November and December 2024, lower is better", css="subtitle"),
    ]
    for tick in (1.5, 2.0, 2.5, 3.0):
        x = left + (tick - low) / (high - low) * width
        parts.append(f'  <line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="112" y2="450"/>\n')
        parts.append(_text(x - 10, 476, f"{tick:.1f}", css="subtitle"))
    for index, (identifier, value) in enumerate(zip(identifiers, values, strict=True)):
        y = 128.0 + index * 66.0
        bar_width = max(2.0, (value - low) / (high - low) * width)
        color = "#1565c0" if index == 0 else "#8da9c4"
        parts.append(_text(48, y + 25, labels[identifier]))
        parts.append(
            f'  <rect x="{left:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="34" '
            f'fill="{color}" rx="6"/>\n'
        )
        parts.append(_text(left + bar_width + 10, y + 24, f"{value:.3f}", css="value"))
    parts.append(f'  <line class="axis" x1="{left}" x2="{left + width}" y1="450" y2="450"/>\n')
    return _document(
        "Held-out interval negative log likelihood",
        (
            "The promoted full model has the lowest interval negative log likelihood "
            "among the five shown frozen bundles."
        ),
        "".join(parts),
    )


def _point_comparison(report: dict[str, Any]) -> str:
    diagnostics = report["point_diagnostics"]["models"]
    series = (
        ("PROMOTED_P50", "Promoted model p50", "#1565c0"),
        ("EMPIRICAL_MIDPOINT", "Empirical midpoint", "#7293a0"),
        ("OFFICIAL_SCHEDULE", "Official schedule", "#a8b8c4"),
    )
    low = 25.0
    high = 42.0
    left = 330.0
    width = 540.0
    parts = [
        _text(48, 54, "Point diagnostic on common eligible rows", css="title"),
        _text(
            48,
            82,
            "Mean absolute distance to the observed arrival interval, seconds",
            css="subtitle",
        ),
    ]
    for tick in (25, 30, 35, 40):
        x = left + (tick - low) / (high - low) * width
        parts.append(f'  <line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="118" y2="388"/>\n')
        parts.append(_text(x - 8, 418, str(tick), css="subtitle"))
    for index, (identifier, label, color) in enumerate(series):
        metric = diagnostics[identifier]["mean_absolute_interval_distance_seconds"]
        value = float(metric["estimate"])
        ci_low = float(metric["lower_95"])
        ci_high = float(metric["upper_95"])
        y = 155.0 + index * 82.0
        x = left + (value - low) / (high - low) * width
        x_low = left + (ci_low - low) / (high - low) * width
        x_high = left + (ci_high - low) / (high - low) * width
        parts.append(_text(48, y + 6, label))
        parts.append(
            f'  <line x1="{x_low:.1f}" x2="{x_high:.1f}" '
            f'y1="{y:.1f}" y2="{y:.1f}" stroke="{color}" '
            'stroke-width="6" stroke-linecap="round"/>\n'
        )
        parts.append(f'  <circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{color}"/>\n')
        parts.append(_text(x_high + 12, y + 6, f"{value:.2f}", css="value"))
    parts.append(
        _text(48, 474, "Whiskers are 95% complete-service-day bootstrap intervals.", css="subtitle")
    )
    parts.append(
        _text(48, 498, "All three estimates use 157,112 common finite-upper rows.", css="subtitle")
    )
    return _document(
        "Mean absolute interval distance comparison",
        (
            "The promoted p50 has a lower mean absolute interval distance than the empirical "
            "midpoint and official schedule on common eligible rows."
        ),
        "".join(parts),
    )


def _calibration(report: dict[str, Any]) -> str:
    rows = report["calibration"]["FULL-normal-scale-0p5"]
    points = [
        (int(row["horizon_seconds"]), float(row["expected_calibration_error"])) for row in rows
    ]
    left = 100.0
    top = 125.0
    width = 780.0
    height = 280.0
    maximum = 0.03
    parts = [
        _text(48, 54, "Promoted-model calibration error", css="title"),
        _text(48, 82, "Expected calibration error at each frozen horizon", css="subtitle"),
    ]
    for tick in (0.00, 0.01, 0.02, 0.03):
        y = top + height - tick / maximum * height
        parts.append(
            f'  <line class="grid" x1="{left}" x2="{left + width}" y1="{y:.1f}" y2="{y:.1f}"/>\n'
        )
        parts.append(_text(48, y + 5, f"{tick:.2f}", css="subtitle"))
    positions: list[tuple[float, float]] = []
    for index, (horizon, value) in enumerate(points):
        x = left + index / (len(points) - 1) * width
        y = top + height - value / maximum * height
        positions.append((x, y))
        parts.append(_text(x - 20, 438, f"{horizon // 60}m", css="subtitle"))
    path = " ".join(
        ("M" if index == 0 else "L") + f" {x:.1f} {y:.1f}" for index, (x, y) in enumerate(positions)
    )
    parts.append(f'  <path d="{path}" fill="none" stroke="#1565c0" stroke-width="5"/>\n')
    for (x, y), (_, value) in zip(positions, points, strict=True):
        parts.append(f'  <circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="#1565c0"/>\n')
        parts.append(_text(x - 20, y - 14, f"{value:.3f}", css="value"))
    parts.append(
        _text(
            48,
            492,
            "All seven horizons are supported in the immutable final report.",
            css="subtitle",
        )
    )
    return _document(
        "Promoted model expected calibration error",
        (
            "Expected calibration error is below 0.026 at every frozen horizon and approaches "
            "zero for long horizons."
        ),
        "".join(parts),
    )


def build_outputs(report: dict[str, Any]) -> dict[Path, str]:
    """Return every deterministic chart body keyed by its output path."""

    return {
        ASSET_ROOT / "calibration-ece.svg": _calibration(report),
        ASSET_ROOT / "model-comparison.svg": _model_comparison(report),
        ASSET_ROOT / "point-comparison.svg": _point_comparison(report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    outputs = build_outputs(report)
    stale: list[str] = []
    for path, body in outputs.items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != body:
                stale.append(path.relative_to(ROOT).as_posix())
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        print(path.relative_to(ROOT))
    if stale:
        print("stale documentation assets: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
