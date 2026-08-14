"""Allow-list-only operational events with trip route-template normalization."""

from __future__ import annotations

import re
from collections.abc import Callable

type AuditEvent = dict[str, str | int]
type AuditSink = Callable[[AuditEvent], None]

_TRIP_PATH = re.compile(r"^/v1/trips/[^/]+(?P<suffix>/state|/events|/stop)?$")
_PUBLIC_PATHS = frozenset(
    {
        "/",
        "/v1/journeys/search",
        "/v1/methodology",
        "/v1/models/active",
        "/v1/openapi.json",
        "/v1/stations",
        "/v1/system/status",
        "/v1/trips",
    }
)


def route_template(path: str) -> str:
    match = _TRIP_PATH.fullmatch(path)
    if match is not None:
        return "/v1/trips/{trip_id}" + (match.group("suffix") or "")
    if path in _PUBLIC_PATHS:
        return path
    return "UNMATCHED_ROUTE"


def access_event(*, method: str, path: str, status_code: int) -> AuditEvent:
    """Build an event exclusively from low-cardinality allow-listed fields."""

    return {
        "event": "HTTP_RESPONSE",
        "method": method if method in {"GET", "HEAD", "OPTIONS", "POST"} else "OTHER",
        "route": route_template(path),
        "status_code": status_code,
    }
