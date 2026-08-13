"""Deterministic, noncausal explanation templates."""

from __future__ import annotations

TEMPLATES = {
    "SHORT_TRANSFER_BUFFER": "The scheduled transfer buffer is short.",
    "LONG_BACKUP_WAIT": "The next scheduled departure has a long wait.",
    "HEADWAY_VARIABILITY_ELEVATED": "Recent observed headways vary more than usual.",
    "CURRENT_DELAY_ELEVATED": "The current observed delay is elevated.",
    "ACTIVE_SERVICE_ALERT": "A current service alert applies to this journey.",
    "LIVE_FEED_STALE": "Live feed data is stale.",
    "HISTORICAL_SUPPORT_SPARSE": "Historical support is too sparse for a numeric estimate.",
    "EXTRA_TIME_FOR_RELIABILITY": "This option adds scheduled time for reliability.",
}


def explanation_text(code: str) -> str:
    try:
        return TEMPLATES[code]
    except KeyError as error:
        raise ValueError("unknown explanation code") from error
