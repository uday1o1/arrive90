"""Append-only alert revision history with point-in-time reads."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from arrive90_data_contracts.realtime import require_utc
from arrive90_data_contracts.schedule import AlertRevision


class AlertRevisionHistory:
    """Retain every alert version and expose the latest known revision per alert."""

    def __init__(self, revisions: Iterable[AlertRevision] = ()) -> None:
        self._revisions: dict[tuple[str, int], AlertRevision] = {}
        for revision in revisions:
            self.append(revision)

    def append(self, revision: AlertRevision) -> None:
        key = (revision.alert_id, revision.revision_number)
        if key in self._revisions:
            raise ValueError("alert revision is immutable and already exists")
        prior = [item for item in self._revisions.values() if item.alert_id == revision.alert_id]
        if prior:
            latest = max(prior, key=lambda item: item.revision_number)
            if revision.revision_number != latest.revision_number + 1:
                raise ValueError("alert revisions must be appended without gaps")
            if revision.product_available_at_utc < latest.product_available_at_utc:
                raise ValueError("alert product availability cannot regress")
        elif revision.revision_number != 1:
            raise ValueError("the first retained alert revision must be revision 1")
        self._revisions[key] = revision

    def all(self) -> tuple[AlertRevision, ...]:
        return tuple(
            sorted(
                self._revisions.values(),
                key=lambda item: (item.alert_id.encode(), item.revision_number),
            )
        )

    def at(self, cutoff_utc: datetime) -> tuple[AlertRevision, ...]:
        require_utc(cutoff_utc, "cutoff_utc")
        eligible = [
            revision
            for revision in self._revisions.values()
            if revision.product_available_at_utc <= cutoff_utc
        ]
        latest: dict[str, AlertRevision] = {}
        for revision in eligible:
            current = latest.get(revision.alert_id)
            if current is None or revision.revision_number > current.revision_number:
                latest[revision.alert_id] = revision
        return tuple(latest[key] for key in sorted(latest, key=str.encode))
