from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arrive90_ingestion.temporal import FutureAccessError, TemporalRecord, TemporalView

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def test_temporal_view_returns_latest_available_version_only() -> None:
    old = TemporalRecord("alert", NOW, NOW, "old")
    future = TemporalRecord("alert", NOW, NOW + timedelta(minutes=1), "future")
    view = TemporalView((future, old), NOW)
    assert view.available() == (old,)
    assert view.get("alert") == old


def test_deliberate_future_access_and_missing_key_fail_differently() -> None:
    future = TemporalRecord("future", NOW, NOW + timedelta(seconds=1), "value")
    view = TemporalView((future,), NOW)
    with pytest.raises(FutureAccessError, match="unavailable"):
        view.get("future")
    with pytest.raises(KeyError, match="missing"):
        view.get("missing")


def test_temporal_contract_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="event_time_utc"):
        TemporalRecord("key", NOW.replace(tzinfo=None), NOW, "value")
    with pytest.raises(ValueError, match="product_available_at_utc"):
        TemporalRecord("key", NOW, NOW.replace(tzinfo=None), "value")
    with pytest.raises(ValueError, match="cutoff_utc"):
        TemporalView((), NOW.replace(tzinfo=None))
