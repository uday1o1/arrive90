from pathlib import Path

WEB = Path("packages/service/src/arrive90_service/web")


def test_frontend_is_outcome_blind_until_explicit_reveal() -> None:
    script = (WEB / "app.js").read_text(encoding="utf-8")
    before_reveal, after_reveal = script.split("async function revealOutcome", maxsplit=1)

    assert "/outcome" not in before_reveal
    assert "/outcome" in after_reveal
    assert "localStorage" not in script
    assert "innerHTML" not in script
    assert "document.cookie" not in script


def test_frontend_has_text_alternatives_and_accessible_controls() -> None:
    page = (WEB / "index.html").read_text(encoding="utf-8")

    for label in (
        "Line",
        "Direction",
        "Origin station",
        "Destination station",
        "Prediction horizon",
        "Held-out replay",
    ):
        assert label in page
    for identifier in (
        'id="cdf-table"',
        'id="calibration-table"',
        'id="interval-text"',
        'id="cutoff-history"',
        'id="outcome-panel"',
        'aria-live="polite"',
    ):
        assert identifier in page
    assert "color alone" in page.lower()
