from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from arrive90_data_contracts.schedule import AlertEffect, AlertRevision
from arrive90_ingestion.alerts import AlertRevisionHistory

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _revision(number: int, available_at: datetime) -> AlertRevision:
    return AlertRevision(
        "alert",
        number,
        f"attempt-{number}",
        available_at,
        available_at,
        NOW,
        NOW + timedelta(hours=1),
        ("route:Red",),
        AlertEffect.NO_SERVICE,
        f"{number:064x}",
    )


def test_alert_history_preserves_revisions_and_hides_future_changes() -> None:
    first = _revision(1, NOW)
    second = _revision(2, NOW + timedelta(minutes=1))
    history = AlertRevisionHistory((first, second))
    assert history.all() == (first, second)
    assert history.at(NOW) == (first,)
    assert history.at(NOW + timedelta(minutes=1)) == (second,)


def test_alert_history_rejects_overwrite_gaps_and_availability_regression() -> None:
    first = _revision(1, NOW)
    history = AlertRevisionHistory((first,))
    with pytest.raises(ValueError, match="already exists"):
        history.append(first)
    with pytest.raises(ValueError, match="without gaps"):
        history.append(_revision(3, NOW + timedelta(minutes=2)))
    with pytest.raises(ValueError, match="cannot regress"):
        history.append(_revision(2, NOW - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="first retained"):
        AlertRevisionHistory((replace(first, alert_id="other", revision_number=2),))


def test_alert_cutoff_must_be_utc() -> None:
    with pytest.raises(ValueError, match="cutoff_utc"):
        AlertRevisionHistory().at(NOW.replace(tzinfo=None))
