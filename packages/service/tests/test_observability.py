from arrive90_service.observability import access_event, route_template


def test_observability_uses_only_route_templates_and_allow_listed_values() -> None:
    trip_id = "secret-trip-id"
    event = access_event(
        method="POST",
        path=f"/v1/trips/{trip_id}/state",
        status_code=401,
    )
    assert event == {
        "event": "HTTP_RESPONSE",
        "method": "POST",
        "route": "/v1/trips/{trip_id}/state",
        "status_code": 401,
    }
    serialized = str(event)
    assert trip_id not in serialized
    assert "station" not in serialized
    assert route_template("/v1/trips/not-a-secret/events") == "/v1/trips/{trip_id}/events"
    assert route_template("/unknown/with-sensitive-value") == "UNMATCHED_ROUTE"
    assert access_event(method="DELETE", path="/", status_code=405)["method"] == "OTHER"
