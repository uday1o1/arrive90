from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
import zipfile
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from arrive90_evaluation import milestone0 as m0
from arrive90_ingestion.rapid_transit_archive import sha256_file


def _version(day: date = date(2022, 1, 3)) -> m0.ScheduleVersion:
    return m0.ScheduleVersion(
        active_start=day,
        active_end=day,
        published_at_utc=datetime(2022, 1, 2, tzinfo=UTC),
        feed_version="MBTA, 2022-01-02T00:00:00+00:00, fixture",
    )


def _stop(
    stop_id: str,
    parent: str,
    sequence: int,
    seconds: int,
) -> m0.ScheduledStop:
    return m0.ScheduledStop(stop_id, parent, sequence, seconds, seconds)


def _trip(
    trip_id: str = "trip-1",
    *,
    route: str = "Red",
    direction: int = 0,
    pattern: str = "pattern-1",
    service: str = "weekday",
    stops: tuple[m0.ScheduledStop, ...] | None = None,
) -> m0.ScheduledTrip:
    return m0.ScheduledTrip(
        date(2022, 1, 3),
        date(2022, 1, 3),
        route,
        direction,
        trip_id,
        pattern,
        service,
        stops
        or (
            _stop("s1", "p1", 1, 8 * 3600),
            _stop("s2", "p2", 2, 8 * 3600 + 300),
            _stop("s3", "p3", 3, 8 * 3600 + 600),
            _stop("s4", "p4", 4, 8 * 3600 + 900),
        ),
    )


def _query(
    *,
    query_id: str = "query-1",
    ready: datetime | None = None,
    legs: tuple[m0.AuditLeg, ...] | None = None,
) -> m0.AuditQuery:
    ready = ready or datetime(2022, 1, 3, 13, tzinfo=UTC)
    return m0.AuditQuery(
        query_id=query_id,
        kind="DIRECT",
        service_date=date(2022, 1, 3),
        query_time_utc=ready - timedelta(minutes=5),
        ready_at_utc=ready,
        observation_horizon_utc=ready + timedelta(hours=3, minutes=30),
        schedule_version_id=_version().version_id,
        slice_name="peak",
        legs=legs or (m0.AuditLeg("Red", 0, "s1", "p1", "s3", "p3", "pattern-1"),),
        transfer_walk_seconds=180,
    )


def _visit(
    stop_id: str,
    sequence: int,
    upper: datetime,
    *,
    lower: datetime | None = None,
    departed: datetime | None = None,
) -> m0.StopVisit:
    return m0.StopVisit(
        stop_id,
        sequence,
        upper,
        departed,
        lower,
        f"events.csv:{sequence}",
        f"events.csv:{sequence + 100}" if departed else None,
    )


def _run(
    *,
    trip_id: str = "trip-1",
    vehicle: str = "vehicle-1",
    ambiguous: bool = False,
    visits: tuple[m0.StopVisit, ...] | None = None,
) -> m0.ObservedRun:
    base = datetime(2022, 1, 3, 13, tzinfo=UTC)
    return m0.ObservedRun(
        date(2022, 1, 3),
        "Red",
        0,
        trip_id,
        vehicle,
        visits
        or (
            _visit("s1", 1, base, departed=base + timedelta(seconds=30)),
            _visit("s2", 2, base + timedelta(minutes=5), lower=base + timedelta(seconds=30)),
            _visit("s3", 3, base + timedelta(minutes=10), lower=base + timedelta(minutes=5)),
        ),
        ambiguous,
    )


def _active_schedule_database(path: Path, *, conflict: bool = False) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE stops (
              stop_id TEXT, parent_station TEXT,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE trips (
              route_id TEXT, direction_id INTEGER, trip_id TEXT,
              route_pattern_id TEXT, service_id TEXT,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE stop_times (
              trip_id TEXT, stop_id TEXT, stop_sequence INTEGER,
              arrival_time TEXT, departure_time TEXT,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE calendar (
              service_id TEXT, start_date INTEGER, end_date INTEGER,
              monday INTEGER, tuesday INTEGER, wednesday INTEGER,
              thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            CREATE TABLE calendar_dates (
              service_id TEXT, date INTEGER, exception_type INTEGER,
              gtfs_active_date INTEGER, gtfs_end_date INTEGER
            );
            """
        )
        connection.executemany(
            "INSERT INTO stops VALUES (?, ?, 20220101, 20220131)",
            (("s1", "p1"), ("s2", "p2"), ("s3", "p3"), ("s4", "p4")),
        )
        connection.execute(
            "INSERT INTO trips VALUES "
            "('Red', 0, 'trip-1', 'pattern-1', 'weekday', 20220103, 20220103)"
        )
        if conflict:
            connection.execute(
                "INSERT INTO trips VALUES "
                "('Red', 1, 'trip-1', 'pattern-2', 'weekday', 20220103, 20220103)"
            )
        connection.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 20220101, 20220131)",
            (
                ("trip-1", "s1", 1, "08:00:00", "08:00:00"),
                ("trip-1", "s2", 2, "08:05:00", "08:05:00"),
                ("trip-1", "s3", 3, "08:10:00", "08:10:00"),
                ("trip-1", "s4", 4, "08:15:00", "08:15:00"),
            ),
        )
        connection.execute(
            "INSERT INTO calendar VALUES "
            "('weekday', 20220101, 20220131, 1, 1, 1, 1, 1, 0, 0, 20220101, 20220131)"
        )
        connection.execute(
            "INSERT INTO calendar_dates VALUES ('weekday', 20220103, 1, 20220101, 20220131)"
        )
    return path


def test_schedule_profile_parser_and_primitives(tmp_path: Path) -> None:
    profile_path = tmp_path / "schedule.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "source_profile_version": "fixture-v1",
                "source_id": "fixture",
                "expanded_size_bytes": 10,
                "expanded_sha256": "a" * 64,
                "compressed_size_bytes": 5,
                "compressed_sha256": "b" * 64,
                "last_modified_at_utc": "2024-01-01T00:00:00+00:00",
                "expected_schedule_version_count": 1,
                "expected_first_active_date": "2022-01-01",
                "expected_last_active_date": "2022-12-31",
                "required_tables": ["feed_info"],
                "rail_routes": list(m0.RAIL_ROUTES),
                "license": {"license_sha256": "c" * 64},
            }
        ),
        encoding="utf-8",
    )
    profile = m0.load_schedule_source_profile(profile_path)
    assert profile.first_active_date == date(2022, 1, 1)
    assert profile.rail_routes == m0.RAIL_ROUTES
    assert m0._parse_feed_version("MBTA, 2022-01-01T00:00:00+00:00, fixture") == datetime(
        2022, 1, 1, tzinfo=UTC
    )
    assert m0._parse_gtfs_seconds("25:01:02") == 90062
    assert m0._parse_gtfs_seconds("") is None
    assert m0._version_key(20220101, 20220102) == (date(2022, 1, 1), date(2022, 1, 2))
    assert m0._canonical_json({"b": 1, "a": 2}).startswith(b'{\n  "a"')

    invalid = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    invalid["rail_routes"] = ["Red"]
    profile_path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
    with pytest.raises(m0.Milestone0AuditError, match="rail routes"):
        m0.load_schedule_source_profile(profile_path)
    with pytest.raises(m0.Milestone0AuditError, match="publication timestamp"):
        m0._parse_feed_version("invalid")
    with pytest.raises(m0.Milestone0AuditError, match="invalid GTFS time"):
        m0._parse_gtfs_seconds("08:90:00")
    with pytest.raises(m0.Milestone0AuditError, match="timezone-aware UTC"):
        m0._utc("2022-01-01T00:00:00", "fixture")


def test_query_cache_is_copy_on_write_and_reused(tmp_path: Path) -> None:
    source = _active_schedule_database(tmp_path / "source.db")
    source_hash = sha256_file(source)
    cache = m0.prepare_schedule_query_cache(
        source,
        source_sha256=source_hash,
        cache_directory=tmp_path / "cache",
    )
    assert cache != source
    assert m0._valid_schedule_query_cache(cache, source_hash)
    assert (
        m0.prepare_schedule_query_cache(
            source,
            source_sha256=source_hash,
            cache_directory=tmp_path / "cache",
        )
        == cache
    )
    with sqlite3.connect(source) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='arrive90_query_cache_metadata'"
            ).fetchone()
            is None
        )
    cache.write_bytes(b"broken")
    rebuilt = m0.prepare_schedule_query_cache(
        source,
        source_sha256=source_hash,
        cache_directory=tmp_path / "cache",
    )
    assert m0._valid_schedule_query_cache(rebuilt, source_hash)


def test_schedule_archive_and_active_schedule_are_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE feed_info "
            "(feed_version TEXT, gtfs_active_date INTEGER, gtfs_end_date INTEGER)"
        )
        connection.execute(
            "INSERT INTO feed_info VALUES "
            "('MBTA, 2022-01-02T00:00:00+00:00, fixture', 20220103, 20220103)"
        )
    compressed = tmp_path / "archive.db.gz"
    compressed.write_bytes(b"compressed")
    profile = m0.ScheduleSourceProfile(
        tmp_path / "profile.yaml",
        "fixture-v1",
        "fixture",
        database.stat().st_size,
        sha256_file(database),
        compressed.stat().st_size,
        sha256_file(compressed),
        datetime(2024, 1, 1, tzinfo=UTC),
        1,
        date(2022, 1, 3),
        date(2022, 1, 3),
        ("feed_info",),
        m0.RAIL_ROUTES,
        "c" * 64,
    )
    report, versions, by_date = m0.audit_schedule_archive(
        profile,
        compressed_archive=compressed,
        expanded_database=database,
    )
    assert report["status"] == "PASSED"
    assert versions == (_version(),)
    assert by_date[date(2022, 1, 3)].version_id == _version().version_id

    compressed.write_bytes(b"drift")
    with pytest.raises(m0.Milestone0AuditError, match="size is not pinned"):
        m0.audit_schedule_archive(
            profile,
            compressed_archive=compressed,
            expanded_database=database,
        )

    active_database = _active_schedule_database(tmp_path / "active.db")
    with sqlite3.connect(active_database) as connection:
        connection.row_factory = sqlite3.Row
        schedule = m0.load_active_schedule(connection, _version())
    assert [trip.trip_id for trip in schedule.trips] == ["trip-1"]
    assert schedule.station_routes["p1"] == frozenset({"Red"})
    assert m0._service_is_active(schedule, schedule.trips[0], date(2022, 1, 3))

    conflict_database = _active_schedule_database(tmp_path / "conflict.db", conflict=True)
    with (
        sqlite3.connect(conflict_database) as connection,
        pytest.raises(m0.Milestone0AuditError, match="conflicting trip identity"),
    ):
        m0.load_active_schedule(connection, _version())


def test_query_generation_helpers_cover_direct_and_endpoint_transfers() -> None:
    trip = _trip()
    direct = m0._direct_query(trip, date(2022, 1, 3), _version(), "seed")
    assert direct is not None
    rank, query = direct
    assert rank > 0
    assert query.kind == "DIRECT"
    assert query.legs[0].route_pattern_id == "pattern-1"
    assert (
        m0._direct_query(_trip(stops=trip.stops[:3]), date(2022, 1, 3), _version(), "seed") is None
    )

    departures = m0._departures_for_leg((trip,), date(2022, 1, 3), query.legs[0])
    assert departures[0].trip_id == "trip-1"
    reversed_leg = m0.AuditLeg("Red", 0, "s3", "p3", "s1", "p1", "pattern-1")
    assert m0._departures_for_leg((trip,), date(2022, 1, 3), reversed_leg) == ()

    transfer_trip = _trip(
        stops=(
            _stop("origin", "origin-parent", 1, 8 * 3600),
            _stop("transfer", "place-dwnxg", 2, 8 * 3600 + 300),
            _stop("destination", "destination-parent", 3, 8 * 3600 + 600),
            _stop("last", "last-parent", 4, 8 * 3600 + 900),
        )
    )
    calls = tuple(m0._transfer_calls(transfer_trip, date(2022, 1, 3)))
    assert len(calls) == 1
    assert calls[0][1] is not None and calls[0][2] is not None
    assert m0._transfer_pair_allowed("place-dwnxg", "Red", "Orange")
    assert not m0._transfer_pair_allowed("place-dwnxg", "Red", "Blue")

    endpoint = _trip(
        stops=(
            _stop("transfer", "place-dwnxg", 1, 8 * 3600),
            _stop("destination", "destination-parent", 2, 8 * 3600 + 300),
        )
    )
    endpoint_call = next(m0._transfer_calls(endpoint, date(2022, 1, 3)))
    assert endpoint_call[1] is None
    assert endpoint_call[2] is not None


def test_complete_query_inventory_generation_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_date = date(2022, 1, 3)
    version = _version(service_date)
    trips: list[m0.ScheduledTrip] = []
    for route in m0.RAIL_ROUTES:
        for slice_name in ("peak", "off_peak"):
            trips.extend(
                _trip(
                    f"direct-{route}-{slice_name}-{index:02d}",
                    route=route,
                    pattern=f"direct-pattern-{route}-{slice_name}",
                )
                for index in range(50)
            )
    for station, pairs in m0.TRANSFER_ROUTE_PAIRS.items():
        first_route, second_route = pairs[0]
        for index in range(25):
            trips.append(
                _trip(
                    f"in-{station}-{index:02d}",
                    route=first_route,
                    pattern=f"in-pattern-{station}",
                )
            )
            trips.append(
                _trip(
                    f"out-{station}-{index:02d}",
                    route=second_route,
                    pattern=f"out-pattern-{station}",
                )
            )
    schedule = m0.ActiveSchedule(
        tuple(trips),
        {},
        {},
        {"p1": frozenset(m0.RAIL_ROUTES)},
    )
    monkeypatch.setattr(m0, "_connect_read_only", lambda _path: nullcontext(object()))
    monkeypatch.setattr(m0, "load_active_schedule", lambda _connection, _version: schedule)
    monkeypatch.setattr(m0, "_service_is_active", lambda *_args: True)

    def direct_query(
        trip: m0.ScheduledTrip,
        _service_date: date,
        _version: m0.ScheduleVersion,
        _seed: str,
    ) -> tuple[int, m0.AuditQuery] | None:
        if not trip.trip_id.startswith("direct-"):
            return None
        slice_name = "off_peak" if "-off_peak-" in trip.trip_id else "peak"
        index = int(trip.trip_id.rsplit("-", 1)[1])
        leg = m0.AuditLeg(
            trip.route_id,
            trip.direction_id,
            "s1",
            "p1",
            "s3",
            "p3",
            trip.route_pattern_id,
        )
        query = _query(query_id=trip.trip_id, legs=(leg,))
        return index, m0.AuditQuery(
            **{
                **query.__dict__,
                "schedule_version_id": version.version_id,
                "slice_name": slice_name,
            }
        )

    def transfer_calls(
        trip: m0.ScheduledTrip,
        _service_date: date,
    ) -> tuple[
        tuple[
            str,
            m0._TransferLegCandidate | None,
            m0._TransferLegCandidate | None,
        ],
        ...,
    ]:
        if not trip.trip_id.startswith(("in-", "out-")):
            return ()
        prefix, remainder = trip.trip_id.split("-", 1)
        station, index_text = remainder.rsplit("-", 1)
        index = int(index_text)
        base = datetime(2022, 1, 3, 13, tzinfo=UTC) + timedelta(seconds=index)
        if prefix == "in":
            leg = m0.AuditLeg(
                trip.route_id,
                0,
                f"origin-{station}",
                f"origin-parent-{station}",
                f"transfer-{station}",
                station,
                trip.route_pattern_id,
            )
            candidate = m0._TransferLegCandidate(
                trip.trip_id,
                service_date,
                base,
                base + timedelta(minutes=10),
                leg,
            )
            return ((station, candidate, None),)
        leg = m0.AuditLeg(
            trip.route_id,
            0,
            f"transfer-{station}",
            station,
            f"destination-{station}",
            f"destination-parent-{station}",
            trip.route_pattern_id,
        )
        candidate = m0._TransferLegCandidate(
            trip.trip_id,
            service_date,
            base + timedelta(minutes=13),
            base + timedelta(minutes=23),
            leg,
        )
        return ((station, None, candidate),)

    monkeypatch.setattr(m0, "_direct_query", direct_query)
    monkeypatch.setattr(m0, "_transfer_calls", transfer_calls)
    monkeypatch.setattr(
        m0,
        "_departures_for_leg",
        lambda _trips, day, leg: (
            m0.ScheduledDeparture(
                f"expected-{leg.route_pattern_id}",
                datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=13),
                datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=14),
            ),
        ),
    )

    queries, station_routes, expected = m0.generate_audit_queries(
        tmp_path / "unused.db",
        (version,),
        public_seed="seed",
        sample_dates={service_date},
    )
    assert len(queries) == 975
    assert len({query.query_id for query in queries}) == 975
    assert len(expected) == 1150
    assert station_routes["p1"] == frozenset(m0.RAIL_ROUTES)


def test_incomplete_query_inventory_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = m0.ActiveSchedule((), {}, {}, {})
    monkeypatch.setattr(m0, "_connect_read_only", lambda _path: nullcontext(object()))
    monkeypatch.setattr(m0, "load_active_schedule", lambda _connection, _version: schedule)
    with pytest.raises(m0.Milestone0AuditError, match="inventory is incomplete"):
        m0.generate_audit_queries(
            tmp_path / "unused.db",
            (_version(),),
            public_seed="seed",
            sample_dates={date(2022, 1, 3)},
        )


def test_observed_event_archive_excludes_predictions_and_scopes_vehicle_identity(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "events.zip"
    fields = list(m0.EVENT_FIELDS)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    base = int(datetime(2022, 1, 3, 13, tzinfo=UTC).timestamp())
    rows = [
        ("s1", 1, "ARR", base),
        ("s1", 1, "DEP", base + 30),
        ("s2", 2, "ARR", base + 300),
        ("s2", 2, "PRA", base + 299),
        ("s3", 3, "ARR", base + 600),
    ]
    for stop_id, sequence, event_type, event_time in rows:
        writer.writerow(
            {
                "service_date": "2022-01-03",
                "route_id": "Red",
                "trip_id": "trip-1",
                "direction_id": 0,
                "stop_id": stop_id,
                "stop_sequence": sequence,
                "vehicle_id": "vehicle-1",
                "vehicle_label": "1",
                "event_type": event_type,
                "event_time": event_time,
                "event_time_sec": event_time,
            }
        )
    writer.writerow(
        {
            "service_date": "2022-01-03",
            "route_id": "Red",
            "trip_id": "trip-1",
            "direction_id": 0,
            "stop_id": "s1",
            "stop_sequence": 1,
            "vehicle_id": "vehicle-2",
            "vehicle_label": "2",
            "event_type": "ARR",
            "event_time": base,
            "event_time_sec": base,
        }
    )
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("events.csv", buffer.getvalue())

    query = _query()
    expected = {
        (query.query_id, 0): (
            m0.ScheduledDeparture(
                "trip-1",
                datetime(2022, 1, 3, 13, tzinfo=UTC),
                datetime(2022, 1, 3, 13, 10, tzinfo=UTC),
            ),
        )
    }
    observed, stats = m0.load_relevant_observed_runs(archive, expected, (query,))
    runs = observed[(date(2022, 1, 3), "Red", 0, "trip-1")]
    assert len(runs) == 2
    assert all(run.ambiguous_identity for run in runs)
    assert stats["prediction_rows_excluded"] == 1
    assert stats["rows_scanned"] == 6
    assert runs[0].visits[1].arrival_lower_utc == datetime(2022, 1, 3, 13, 0, 30, tzinfo=UTC)


def test_oracle_requires_direct_presence_and_complete_prior_reconciliation() -> None:
    query = _query()
    scheduled = (
        m0.ScheduledDeparture(
            "missing-prior",
            query.ready_at_utc - timedelta(minutes=5),
            query.ready_at_utc + timedelta(minutes=5),
        ),
        m0.ScheduledDeparture(
            "trip-1",
            query.ready_at_utc,
            query.ready_at_utc + timedelta(minutes=10),
        ),
    )
    expected = {(query.query_id, 0): scheduled}
    observed = {(query.service_date, "Red", 0, "trip-1"): (_run(),)}
    resolution = m0._resolve_leg(
        query=query,
        leg_index=0,
        ready_at_utc=query.ready_at_utc,
        expected=expected,
        observed=observed,
    )
    assert resolution is not None
    assert resolution.reconciliation_complete is False
    assert resolution.missing_prior_scheduled_trip_ids == ("missing-prior",)

    complete = m0._resolve_leg(
        query=query,
        leg_index=0,
        ready_at_utc=query.ready_at_utc,
        expected={(query.query_id, 0): scheduled[1:]},
        observed=observed,
    )
    assert complete is not None and complete.reconciliation_complete
    records = m0.resolve_audit_queries(
        (query,),
        expected={(query.query_id, 0): scheduled[1:]},
        observed=observed,
        schedule_by_date={query.service_date: _version()},
    )
    assert records[0]["status"] == "ARRIVED"
    assert records[0]["arrival_interval_width_seconds"] == 300
    assert records[0]["outcome_time_semantic"] == "CONSERVATIVE_STATION_DEPARTURE_INTERVAL"

    downstream_only = _run(
        visits=(
            _visit("s2", 2, query.ready_at_utc + timedelta(minutes=5)),
            _visit(
                "s3",
                3,
                query.ready_at_utc + timedelta(minutes=10),
                lower=query.ready_at_utc + timedelta(minutes=5),
            ),
        )
    )
    assert (
        m0._resolve_leg(
            query=query,
            leg_index=0,
            ready_at_utc=query.ready_at_utc,
            expected={(query.query_id, 0): scheduled[1:]},
            observed={(query.service_date, "Red", 0, "trip-1"): (downstream_only,)},
        )
        is None
    )


def test_oracle_reconciles_absent_post_horizon_trip_as_nonsuccess() -> None:
    query = _query()
    expected = {
        (query.query_id, 0): (
            m0.ScheduledDeparture(
                "trip-1",
                query.ready_at_utc,
                query.ready_at_utc + timedelta(minutes=10),
            ),
            m0.ScheduledDeparture(
                "post-horizon",
                query.observation_horizon_utc + timedelta(minutes=1),
                query.observation_horizon_utc + timedelta(minutes=10),
            ),
        )
    }
    resolution = m0._resolve_leg(
        query=query,
        leg_index=0,
        ready_at_utc=query.ready_at_utc,
        expected=expected,
        observed={(query.service_date, "Red", 0, "trip-1"): (_run(),)},
    )
    assert resolution is not None
    assert resolution.reconciliation_complete
    assert resolution.missing_prior_scheduled_trip_ids == ()


def test_metrics_and_scope_are_fail_closed() -> None:
    arrived = {
        "kind": "DIRECT",
        "routes": ["Red"],
        "slice": "peak",
        "status": "ARRIVED",
        "arrival_interval_width_seconds": 60,
        "observed_stop_presence_after_ready": True,
        "transfer_parent_station": None,
    }
    censored = {
        **arrived,
        "slice": "off_peak",
        "status": "CENSORED",
        "arrival_interval_width_seconds": None,
        "observed_stop_presence_after_ready": False,
    }
    metrics = m0._record_metrics((arrived, censored))
    assert metrics["audit_candidate_resolution_rate"] == 0.5
    assert metrics["censoring_rate"] == 0.5
    assert metrics["arrival_interval_width_coverage"] == 1.0
    assert m0._record_metrics(())["audit_candidate_resolution_rate"] is None
    assert not m0._passes(None, 0.5)
    assert m0._passes(0.1, 0.2, maximum=True)

    charter = {
        "scope": {
            "line_retention_rule": {
                "resolution_rate_min": 0.9,
                "censoring_rate_max": 0.1,
                "interval_width_coverage_min": 0.9,
                "observed_stop_presence_rate_min": 0.9,
                "peak_and_off_peak_resolution_rate_min": 0.8,
                "peak_and_off_peak_interval_width_coverage_min": 0.8,
            },
            "transfer_station_retention_rule": {
                "query_count_min": 25,
                "resolution_rate_min": 0.8,
                "interval_width_coverage_min": 0.8,
            },
        }
    }
    summary = m0.summarize_query_audit(
        (arrived, censored),
        station_routes={"p1": frozenset({"Red"})},
        charter=charter,
    )
    assert summary["recommended_scope"]["supported_lines"] == []
    assert summary["overall_all_proposed_scope"]["candidate_count"] == 2


def test_sample_dates_include_boundaries_and_discontinuities() -> None:
    dates = m0.build_audit_sample_dates(
        year=2022,
        public_seed="seed",
        versions=(_version(),),
        major_discontinuity_dates=(date(2022, 8, 19),),
    )
    assert date(2022, 1, 3) in dates
    assert date(2022, 8, 16) in dates
    assert date(2022, 8, 22) in dates
    assert len(dates) >= 30


def test_full_audit_writes_failed_evidence_without_claiming_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = tmp_path / "events.zip"
    event.write_bytes(b"event")
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    schedule_archive = tmp_path / "schedule.db.gz"
    schedule_archive.write_bytes(b"schedule")
    schedule_database = tmp_path / "schedule.db"
    schedule_database.write_bytes(b"database")
    license_pdf = tmp_path / "license.pdf"
    license_pdf.write_bytes(b"license")
    producer_root = tmp_path / "producer"
    lamp_root = tmp_path / "lamp"
    producer_root.mkdir()
    lamp_root.mkdir()
    (producer_root / "one").write_bytes(b"one")
    (lamp_root / "dictionary").write_bytes(b"dictionary")
    (lamp_root / "source").write_bytes(b"source")

    event_profile_path = tmp_path / "event.yaml"
    event_profile_path.write_text("event\n", encoding="utf-8")
    schedule_profile_path = tmp_path / "schedule.yaml"
    schedule_profile_path.write_text("schedule\n", encoding="utf-8")
    lamp_profile_path = tmp_path / "lamp.yaml"
    lamp_profile_path.write_text(
        yaml.safe_dump(
            {
                "source_profile_version": "lamp-v1",
                "files": {
                    "dictionary": sha256_file(lamp_root / "dictionary"),
                    "source": sha256_file(lamp_root / "source"),
                },
            }
        ),
        encoding="utf-8",
    )
    acceptance = tmp_path / "acceptance.yaml"
    acceptance.write_text(
        yaml.safe_dump(
            {
                "acceptance_version": "fixture-v1",
                "query_generation": {"public_seed": "seed"},
                "historical_interval": {
                    "representative_sample": {"major_documented_discontinuities": []}
                },
                "scope": {
                    "scope_frozen": False,
                    "supported_lines": [],
                    "supported_stations": [],
                    "supported_transfer_stations": [],
                    "line_retention_rule": {
                        "resolution_rate_min": 0.9,
                        "censoring_rate_max": 0.1,
                        "interval_width_coverage_min": 0.9,
                        "observed_stop_presence_rate_min": 0.9,
                        "peak_and_off_peak_resolution_rate_min": 0.8,
                        "peak_and_off_peak_interval_width_coverage_min": 0.8,
                    },
                    "transfer_station_retention_rule": {
                        "query_count_min": 25,
                        "resolution_rate_min": 0.8,
                        "interval_width_coverage_min": 0.8,
                    },
                },
                "gates": {
                    "audit_candidate_resolution_rate_overall_min": 0.9,
                    "audit_candidate_resolution_rate_slice_min": 0.8,
                    "audit_censoring_rate_overall_max": 0.1,
                    "arrival_interval_width_coverage_overall_min": 0.9,
                    "arrival_interval_width_coverage_slice_min": 0.8,
                },
            }
        ),
        encoding="utf-8",
    )
    provenance = tmp_path / "provenance.yaml"
    provenance.write_text(
        yaml.safe_dump(
            {
                "fields": {f"field-{index}": {} for index in range(5)},
                "operational_feature_families": {
                    "recent_delay": {"status": "EXCLUDED"},
                },
            }
        ),
        encoding="utf-8",
    )

    event_profile = SimpleNamespace(
        expected_size_bytes=event.stat().st_size,
        expected_archive_sha256=sha256_file(event),
        producer_file_hashes={"one": sha256_file(producer_root / "one")},
        expected_modified_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
        expected_license="CC0",
        source_profile_version="events-v1",
    )
    schedule_profile = SimpleNamespace(
        expanded_sha256=sha256_file(schedule_database),
        compressed_sha256=sha256_file(schedule_archive),
        license_sha256=sha256_file(license_pdf),
        last_modified_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
        source_profile_version="schedule-v1",
    )
    monkeypatch.setattr(m0, "load_source_profile", lambda _path: event_profile)
    monkeypatch.setattr(
        m0,
        "validate_metadata",
        lambda _profile, _body: {
            "id": "item",
            "owner": "MBTAHUB_ADMIN",
            "access": "public",
            "licenseInfo": "CC0",
        },
    )
    monkeypatch.setattr(m0, "load_schedule_source_profile", lambda _path: schedule_profile)
    monkeypatch.setattr(
        m0,
        "audit_schedule_archive",
        lambda *_args, **_kwargs: (
            {"status": "PASSED"},
            (_version(),),
            {date(2022, 1, 3): _version()},
        ),
    )
    monkeypatch.setattr(
        m0,
        "prepare_schedule_query_cache",
        lambda *_args, **_kwargs: schedule_database,
    )
    query = _query()
    expected = {
        (query.query_id, 0): (
            m0.ScheduledDeparture(
                "trip-1", query.ready_at_utc, query.ready_at_utc + timedelta(minutes=10)
            ),
        )
    }
    monkeypatch.setattr(
        m0,
        "generate_audit_queries",
        lambda *_args, **_kwargs: (
            (query,),
            {"p1": frozenset({"Red"})},
            expected,
        ),
    )
    monkeypatch.setattr(
        m0,
        "load_relevant_observed_runs",
        lambda *_args, **_kwargs: (
            {(query.service_date, "Red", 0, "trip-1"): (_run(),)},
            {
                "rows_scanned": 5,
                "prediction_rows_excluded": 0,
                "relevant_actual_rows": 5,
                "relevant_semantic_duplicates_removed": 0,
                "required_schedule_trip_identities": 1,
                "observed_schedule_trip_identities": 1,
                "ambiguous_schedule_trip_identities": 0,
            },
        ),
    )
    runtime = tmp_path / "runtime" / "audit.json"
    gate = tmp_path / "reports" / "gate.json"
    report = m0.run_milestone0_audit(
        m0.Milestone0Inputs(
            event_profile_path,
            metadata,
            event,
            datetime(2026, 1, 1, tzinfo=UTC),
            schedule_profile_path,
            schedule_archive,
            schedule_database,
            datetime(2026, 1, 1, tzinfo=UTC),
            acceptance,
            provenance,
            lamp_profile_path,
            lamp_root,
            producer_root,
            license_pdf,
            runtime,
            gate,
        )
    )
    assert report["status"] == "FAILED"
    assert "manual_direct_query_inventory_satisfied" in report["failing_checks"]
    assert json.loads(runtime.read_text(encoding="utf-8"))["query_inventory"]["query_count"] == 1
    assert json.loads(gate.read_text(encoding="utf-8"))["status"] == "FAILED"


def test_cli_returns_nonzero_for_audit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arrive90-audit-milestone0",
            "--event-metadata",
            "missing",
            "--event-archive",
            "missing",
            "--event-acquired-at-utc",
            "2026-01-01T00:00:00+00:00",
            "--schedule-archive",
            "missing",
            "--schedule-database",
            "missing",
            "--schedule-acquired-at-utc",
            "2026-01-01T00:00:00+00:00",
            "--lamp-root",
            "missing",
            "--producer-root",
            "missing",
            "--license",
            "missing",
        ],
    )
    assert m0.main() == 1


def test_cli_returns_zero_for_passing_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arrive90-audit-milestone0",
            "--event-metadata",
            "event-metadata",
            "--event-archive",
            "event-archive",
            "--event-acquired-at-utc",
            "2026-01-01T00:00:00+00:00",
            "--schedule-archive",
            "schedule-archive",
            "--schedule-database",
            "schedule-database",
            "--schedule-acquired-at-utc",
            "2026-01-01T00:00:00+00:00",
            "--lamp-root",
            "lamp",
            "--producer-root",
            "producer",
            "--license",
            "license",
        ],
    )
    monkeypatch.setattr(
        m0,
        "run_milestone0_audit",
        lambda _inputs: {"status": "PASSED", "failing_checks": []},
    )
    assert m0.main() == 0
