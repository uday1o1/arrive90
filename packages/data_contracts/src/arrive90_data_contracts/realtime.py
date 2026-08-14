"""Shared UTC validation for immutable observation boundaries."""

from __future__ import annotations

from datetime import UTC, datetime


def require_utc(value: datetime, field: str) -> None:
    """Reject naive and non-UTC timestamps at storage boundaries."""

    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
