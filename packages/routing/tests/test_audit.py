from __future__ import annotations

import pytest
from arrive90_routing.audit import (
    PlatformCall,
    RoutePattern,
    StaticAuditEnumerator,
    TransferWalkRule,
)


def _pattern(identifier: str, route: str, stations: tuple[str, ...]) -> RoutePattern:
    return RoutePattern(
        identifier,
        route,
        0,
        tuple(PlatformCall(f"{identifier}-{station}", station) for station in stations),
    )


def test_audit_enumerator_finds_direct_and_supported_transfer_policies() -> None:
    red = _pattern("red", "Red", ("a", "x", "b"))
    orange = _pattern("orange", "Orange", ("x", "c"))
    rule = TransferWalkRule("red-x", "orange-x", "x", 180)
    enumerator = StaticAuditEnumerator((orange, red), (rule,))
    direct = enumerator.enumerate("a", "b")
    transfer = enumerator.enumerate("a", "c")
    assert len(direct) == 1
    assert len(transfer) == 1
    assert transfer[0].transfer_walk_seconds == (180,)


def test_unsupported_platform_connectivity_suppresses_transfer() -> None:
    red = _pattern("red", "Red", ("a", "x"))
    orange = _pattern("orange", "Orange", ("x", "c"))
    assert StaticAuditEnumerator((red, orange), ()).enumerate("a", "c") == ()
    with pytest.raises(ValueError, match="at least two"):
        RoutePattern("bad", "Red", 0, (PlatformCall("one", "one"),))
    with pytest.raises(ValueError, match="repeat"):
        RoutePattern(
            "bad",
            "Red",
            0,
            (PlatformCall("same", "a"), PlatformCall("same", "b")),
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        TransferWalkRule("a", "b", "x", -1)
