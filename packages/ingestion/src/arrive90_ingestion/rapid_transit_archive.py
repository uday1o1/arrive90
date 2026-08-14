"""Discover and validate the official MBTA rapid-transit event archive."""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import hmac
import json
import os
import tempfile
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from arrive90_data_contracts.schedule import (
    ArrivalEvidence,
    DepartureEvidence,
    IntervalClosure,
    NormalizedStopEvidence,
)

from arrive90_ingestion.archive import ArchiveLimits, ArchiveRejectedError

NEW_YORK = ZoneInfo("America/New_York")
METADATA_LIMIT_BYTES = 2 * 1024 * 1024
READ_BLOCK_BYTES = 1024 * 1024


class SourceDiscoveryError(ValueError):
    """Raised when a public source no longer matches its pinned contract."""


@dataclass(frozen=True)
class ArchivedRapidTransitEvent:
    """One public event with stable run identity and conservative availability."""

    source_row_key: str
    source_member: str
    service_date: date
    route_id: str
    direction_id: int
    trip_id: str
    vehicle_id: str
    stop_id: str
    stop_sequence: int
    event_type: str
    event_time_utc: datetime
    product_available_at_utc: datetime

    def __post_init__(self) -> None:
        if not all(
            (
                self.source_row_key,
                self.source_member,
                self.route_id,
                self.trip_id,
                self.vehicle_id,
                self.stop_id,
            )
        ):
            raise ValueError("archived event requires complete source and run identity")
        if self.direction_id not in (0, 1) or self.stop_sequence < 0:
            raise ValueError("archived event direction or stop sequence is invalid")
        if self.event_type not in {"ARR", "DEP", "PRA", "PRD"}:
            raise ValueError("archived event type is unknown")
        for field, value in (
            ("event_time_utc", self.event_time_utc),
            ("product_available_at_utc", self.product_available_at_utc),
        ):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                raise ValueError(f"{field} must be timezone-aware UTC")
        if self.product_available_at_utc < self.event_time_utc:
            raise ValueError("archived event cannot be available before it was observed")


def normalize_archived_arrivals(
    events: tuple[ArchivedRapidTransitEvent, ...],
) -> tuple[NormalizedStopEvidence, ...]:
    """Build direct upper-bound evidence while quarantining ambiguous run identity."""

    trip_vehicles: dict[tuple[date, str, int, str], set[str]] = {}
    grouped: dict[tuple[date, str, int, str, str], list[ArchivedRapidTransitEvent]] = {}
    for event in events:
        trip_key = (event.service_date, event.route_id, event.direction_id, event.trip_id)
        trip_vehicles.setdefault(trip_key, set()).add(event.vehicle_id)
        run_key = (*trip_key, event.vehicle_id)
        grouped.setdefault(run_key, []).append(event)
    ambiguous = [key for key, vehicles in trip_vehicles.items() if len(vehicles) != 1]
    if ambiguous:
        raise SourceDiscoveryError("archived trip identity is reused across vehicles")

    normalized: list[NormalizedStopEvidence] = []
    for _run_key, run_events in sorted(grouped.items()):
        ordered = sorted(
            run_events,
            key=lambda event: (
                event.event_time_utc,
                event.stop_sequence,
                event.event_type,
                event.source_row_key,
            ),
        )
        prior_departures: list[ArchivedRapidTransitEvent] = []
        maximum_actual_sequence = -1
        for event in ordered:
            if event.event_type in {"PRA", "PRD"}:
                continue
            if event.stop_sequence < maximum_actual_sequence:
                raise SourceDiscoveryError("archived run has contradictory stop ordering")
            maximum_actual_sequence = max(maximum_actual_sequence, event.stop_sequence)
            if event.event_type == "DEP":
                prior_departures.append(event)
                continue
            lower_candidates = [
                departure
                for departure in prior_departures
                if departure.stop_sequence < event.stop_sequence
                and departure.event_time_utc < event.event_time_utc
            ]
            lower = max((candidate.event_time_utc for candidate in lower_candidates), default=None)
            normalized.append(
                NormalizedStopEvidence(
                    source_row_key=event.source_row_key,
                    service_date=event.service_date,
                    observed_trip_id=event.trip_id,
                    observed_vehicle_id=event.vehicle_id,
                    stop_id=event.stop_id,
                    stop_sequence=event.stop_sequence,
                    arrival_lower_bound_utc=lower,
                    arrival_upper_bound_utc=event.event_time_utc,
                    arrival_interval_closed=IntervalClosure.LEFT_OPEN_RIGHT_CLOSED,
                    arrival_evidence=ArrivalEvidence.VP_STOPPED_AT,
                    departure_upper_bound_utc=None,
                    departure_evidence=DepartureEvidence.UNKNOWN,
                    product_available_at_utc=event.product_available_at_utc,
                    usable_for_primary_boarding=True,
                )
            )
    return tuple(normalized)


@dataclass(frozen=True)
class SourceProfile:
    """Pinned identity, archive, schema, and licensing expectations."""

    profile_path: Path
    source_profile_version: str
    source_id: str
    item_id: str
    metadata_url: str
    archive_url: str
    allowed_host: str
    expected_owner: str
    expected_access: str
    expected_license: str
    expected_name: str
    expected_title: str
    expected_size_bytes: int
    expected_modified_at_utc: datetime
    expected_archive_sha256: str
    archive_root: str
    archive_year: int
    archive_modes: tuple[str, ...]
    expected_member_count: int
    archive_limits: ArchiveLimits
    schema_fields: tuple[str, ...]
    event_types: tuple[str, ...]
    routes: tuple[str, ...]
    producer_repository: str
    producer_commit: str
    producer_file_hashes: Mapping[str, str]
    license_identifier: str
    attribution: str
    raw_archive_redistribution: str
    project_artifact_policy: str

    @property
    def expected_members(self) -> tuple[str, ...]:
        return tuple(
            f"{self.archive_root}/{self.archive_year}-{month:02d}_{mode}Events.csv"
            for month in range(1, 13)
            for mode in self.archive_modes
        )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SourceDiscoveryError(f"{field} must be a string-keyed mapping")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SourceDiscoveryError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SourceDiscoveryError(f"{field} must be a positive integer")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SourceDiscoveryError(f"{field} must be a list")
    parsed = tuple(_string(item, field) for item in value)
    if not parsed or len(parsed) != len(set(parsed)):
        raise SourceDiscoveryError(f"{field} must contain unique strings")
    return parsed


def load_source_profile(path: Path) -> SourceProfile:
    """Load the repository-owned source profile with strict field validation."""

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = _mapping(loaded, "source profile")
    metadata = _mapping(root.get("expected_metadata"), "expected_metadata")
    archive = _mapping(root.get("archive"), "archive")
    schema = _mapping(root.get("schema"), "schema")
    producer = _mapping(root.get("producer_semantics"), "producer_semantics")
    producer_files = _mapping(producer.get("files"), "producer_semantics.files")
    license_data = _mapping(root.get("license"), "license")
    modified = datetime.fromisoformat(_string(metadata.get("modified_at_utc"), "modified_at_utc"))
    if modified.tzinfo is None or modified.utcoffset() != UTC.utcoffset(modified):
        raise SourceDiscoveryError("modified_at_utc must be timezone-aware UTC")
    limits = ArchiveLimits(
        maximum_compressed_bytes=_integer(
            archive.get("maximum_compressed_bytes"), "maximum_compressed_bytes"
        ),
        maximum_expanded_bytes=_integer(
            archive.get("maximum_expanded_bytes"), "maximum_expanded_bytes"
        ),
        maximum_expansion_ratio=float(
            _integer(archive.get("maximum_expansion_ratio"), "maximum_expansion_ratio")
        ),
    )
    hashes = {
        _string(name, "producer file name"): _string(digest, "producer file digest")
        for name, digest in producer_files.items()
    }
    profile = SourceProfile(
        profile_path=path,
        source_profile_version=_string(
            root.get("source_profile_version"), "source_profile_version"
        ),
        source_id=_string(root.get("source_id"), "source_id"),
        item_id=_string(root.get("item_id"), "item_id"),
        metadata_url=_string(root.get("metadata_url"), "metadata_url"),
        archive_url=_string(root.get("archive_url"), "archive_url"),
        allowed_host=_string(root.get("allowed_host"), "allowed_host"),
        expected_owner=_string(metadata.get("owner"), "expected_metadata.owner"),
        expected_access=_string(metadata.get("access"), "expected_metadata.access"),
        expected_license=_string(metadata.get("license"), "expected_metadata.license"),
        expected_name=_string(metadata.get("name"), "expected_metadata.name"),
        expected_title=_string(metadata.get("title"), "expected_metadata.title"),
        expected_size_bytes=_integer(metadata.get("size_bytes"), "expected_metadata.size_bytes"),
        expected_modified_at_utc=modified,
        expected_archive_sha256=_string(archive.get("sha256"), "archive.sha256"),
        archive_root=_string(archive.get("root"), "archive.root"),
        archive_year=_integer(archive.get("year"), "archive.year"),
        archive_modes=_strings(archive.get("modes"), "archive.modes"),
        expected_member_count=_integer(
            archive.get("expected_member_count"), "archive.expected_member_count"
        ),
        archive_limits=limits,
        schema_fields=_strings(schema.get("fields"), "schema.fields"),
        event_types=_strings(schema.get("event_types"), "schema.event_types"),
        routes=_strings(schema.get("routes"), "schema.routes"),
        producer_repository=_string(producer.get("repository"), "producer.repository"),
        producer_commit=_string(producer.get("commit"), "producer.commit"),
        producer_file_hashes=hashes,
        license_identifier=_string(license_data.get("identifier"), "license.identifier"),
        attribution=_string(license_data.get("attribution"), "license.attribution"),
        raw_archive_redistribution=_string(
            license_data.get("raw_archive_redistribution"),
            "license.raw_archive_redistribution",
        ),
        project_artifact_policy=_string(
            license_data.get("project_artifact_policy"), "license.project_artifact_policy"
        ),
    )
    if len(profile.expected_archive_sha256) != 64:
        raise SourceDiscoveryError("archive SHA-256 must contain 64 characters")
    if len(profile.expected_members) != profile.expected_member_count:
        raise SourceDiscoveryError("derived archive member count does not match the profile")
    return profile


def sha256_file(path: Path) -> str:
    """Hash a file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(READ_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_metadata(profile: SourceProfile, body: bytes) -> dict[str, Any]:
    """Reject an ArcGIS item whose identity or public terms drifted."""

    try:
        loaded = json.loads(body)
    except json.JSONDecodeError as error:
        raise SourceDiscoveryError("source metadata is not valid JSON") from error
    metadata = _mapping(loaded, "ArcGIS metadata")
    expected = {
        "id": profile.item_id,
        "owner": profile.expected_owner,
        "access": profile.expected_access,
        "name": profile.expected_name,
        "title": profile.expected_title,
        "size": profile.expected_size_bytes,
    }
    mismatches = {
        field: {"expected": value, "observed": metadata.get(field)}
        for field, value in expected.items()
        if metadata.get(field) != value
    }
    if metadata.get("licenseInfo") != profile.expected_license:
        mismatches["licenseInfo"] = {
            "expected": profile.expected_license,
            "observed": metadata.get("licenseInfo"),
        }
    expected_modified_ms = int(profile.expected_modified_at_utc.timestamp() * 1000)
    if metadata.get("modified") != expected_modified_ms:
        mismatches["modified"] = {
            "expected": expected_modified_ms,
            "observed": metadata.get("modified"),
        }
    if mismatches:
        raise SourceDiscoveryError(f"ArcGIS metadata does not match pinned values: {mismatches}")
    return metadata


def _validate_url(url: str, profile: SourceProfile, expected_url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != profile.allowed_host:
        raise SourceDiscoveryError("source URL must use the pinned HTTPS host")
    if url != expected_url:
        raise SourceDiscoveryError("source URL does not match the pinned path and query")


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, profile: SourceProfile, expected_url: str) -> None:
        super().__init__()
        self.profile = profile
        self.expected_url = expected_url

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_url(newurl, self.profile, self.expected_url)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(profile: SourceProfile, expected_url: str) -> urllib.request.OpenerDirector:
    _validate_url(expected_url, profile, expected_url)
    return urllib.request.build_opener(_PinnedRedirectHandler(profile, expected_url))


def _download_metadata(profile: SourceProfile, destination: Path) -> bytes:
    opener = _opener(profile, profile.metadata_url)
    request = urllib.request.Request(  # noqa: S310 - URL is pinned and validated above.
        profile.metadata_url, headers={"User-Agent": "arrive90-source-discovery/1"}
    )
    with opener.open(request, timeout=30) as response:
        _validate_url(response.geturl(), profile, profile.metadata_url)
        body = cast(bytes, response.read(METADATA_LIMIT_BYTES + 1))
    if len(body) > METADATA_LIMIT_BYTES:
        raise SourceDiscoveryError("source metadata exceeded the byte limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return body


def _download_archive(profile: SourceProfile, destination: Path) -> datetime:
    if destination.exists():
        if (
            destination.stat().st_size != profile.expected_size_bytes
            or sha256_file(destination) != profile.expected_archive_sha256
        ):
            raise SourceDiscoveryError("existing archive path contains unpinned bytes")
        return datetime.fromtimestamp(destination.stat().st_mtime, tz=UTC)
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = _opener(profile, profile.archive_url)
    request = urllib.request.Request(  # noqa: S310 - URL is pinned and validated above.
        profile.archive_url, headers={"User-Agent": "arrive90-source-discovery/1"}
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix="archive-", dir=destination.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            with opener.open(request, timeout=60) as response:
                _validate_url(response.geturl(), profile, profile.archive_url)
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) != profile.expected_size_bytes:
                    raise SourceDiscoveryError("archive Content-Length is not the pinned size")
                while block := response.read(READ_BLOCK_BYTES):
                    total += len(block)
                    if total > profile.archive_limits.maximum_compressed_bytes:
                        raise SourceDiscoveryError("archive exceeded the compressed-size limit")
                    digest.update(block)
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if total != profile.expected_size_bytes:
            raise SourceDiscoveryError("downloaded archive size is not the pinned size")
        if digest.hexdigest() != profile.expected_archive_sha256:
            raise SourceDiscoveryError("downloaded archive digest is not pinned")
        os.replace(temporary, destination)
        return datetime.now(tz=UTC)
    finally:
        temporary.unlink(missing_ok=True)


def _decoded_lines(stream: IO[bytes], digest: Any) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    pending = ""
    while block := stream.read(READ_BLOCK_BYTES):
        digest.update(block)
        pending += decoder.decode(block)
        lines = pending.splitlines(keepends=True)
        pending = lines.pop() if lines and not lines[-1].endswith(("\n", "\r")) else ""
        yield from lines
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending


def _member_month(member: str) -> int:
    filename = member.rsplit("/", maxsplit=1)[-1]
    try:
        return int(filename[5:7])
    except ValueError as error:
        raise SourceDiscoveryError(f"archive member has an invalid month: {member}") from error


def _event_unit_digest(row: Mapping[str, str]) -> bytes:
    fields = (
        "service_date",
        "route_id",
        "trip_id",
        "direction_id",
        "stop_id",
        "stop_sequence",
        "vehicle_id",
        "event_type",
    )
    encoded = "\x1f".join(row[field] for field in fields).encode()
    return hashlib.blake2b(encoded, digest_size=16).digest()


def _semantic_event_digest(row: Mapping[str, str]) -> bytes:
    encoded = b"\x1f".join(
        (
            _event_unit_digest(row),
            row["event_time"].encode(),
        )
    )
    return hashlib.blake2b(encoded, digest_size=16).digest()


def _service_seconds(event_time: int, service_date: date) -> int:
    observed = datetime.fromtimestamp(event_time, tz=UTC).astimezone(NEW_YORK)
    day_offset = (observed.date() - service_date).days
    return day_offset * 86400 + observed.hour * 3600 + observed.minute * 60 + observed.second


def _producer_service_seconds_drift_is_classified(deltas: set[int]) -> bool:
    return bool(deltas) and all(
        min(abs(delta - expected) for expected in (-3600, 0, 3600)) <= 1 for delta in deltas
    )


def _expected_dates(year: int) -> set[date]:
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    return {start + timedelta(days=offset) for offset in range((end - start).days)}


def deterministic_sample_dates(year: int, public_seed: str) -> tuple[date, ...]:
    """Apply the frozen 30-day, monthly-week, DST, and year-boundary rules."""

    available = _expected_dates(year)

    def keyed(label: str, candidate: date) -> bytes:
        message = f"{label}|{candidate.isoformat()}".encode()
        return hmac.digest(public_seed.encode(), message, "sha256")

    starts = sorted(day for day in available if day + timedelta(days=29) in available)
    consecutive_start = min(starts, key=lambda day: (keyed("consecutive-30", day), day))
    selected = {consecutive_start + timedelta(days=offset) for offset in range(30)}
    for month in range(1, 13):
        mondays = sorted(
            day
            for day in available
            if day.month == month
            and day.weekday() == 0
            and (day + timedelta(days=6)).month == month
        )
        week_start = min(
            mondays,
            key=lambda day: (keyed(f"monthly-week|{year}-{month:02d}", day), day),
        )
        selected.update(week_start + timedelta(days=offset) for offset in range(7))
    fixed = (
        date(year, 1, 1),
        date(year, 1, 2),
        date(year, 1, 3),
        date(year, 3, 12),
        date(year, 3, 13),
        date(year, 3, 14),
        date(year, 11, 5),
        date(year, 11, 6),
        date(year, 11, 7),
        date(year, 12, 29),
        date(year, 12, 30),
        date(year, 12, 31),
    )
    selected.update(fixed)
    return tuple(sorted(selected))


def inspect_archive(profile: SourceProfile, archive: Path, public_seed: str) -> dict[str, Any]:
    """Stream every source row and return a complete source-discovery manifest."""

    compressed_size = archive.stat().st_size
    archive_digest = sha256_file(archive)
    if compressed_size > profile.archive_limits.maximum_compressed_bytes:
        raise ArchiveRejectedError("compressed archive size limit exceeded")
    if compressed_size != profile.expected_size_bytes:
        raise SourceDiscoveryError("archive size is not the pinned size")
    if archive_digest != profile.expected_archive_sha256:
        raise SourceDiscoveryError("archive SHA-256 is not the pinned digest")

    all_dates: set[date] = set()
    all_routes: Counter[str] = Counter()
    all_event_types: Counter[str] = Counter()
    member_reports: list[dict[str, Any]] = []
    invalid_identity_rows = 0
    timestamp_mismatches = 0
    repeated_event_units = 0
    duplicate_semantic_events = 0
    timestamp_delta_counts: Counter[int] = Counter()
    timestamp_mismatch_examples: list[dict[str, str | int]] = []
    repeated_event_unit_examples: list[dict[str, str | int]] = []
    duplicate_semantic_event_examples: list[dict[str, str | int]] = []
    expanded_size = 0
    compressed_member_size = 0
    expected_members = set(profile.expected_members)
    with zipfile.ZipFile(archive) as source:
        members = [info for info in source.infolist() if not info.is_dir()]
        observed_members = {info.filename for info in members}
        if observed_members != expected_members or len(members) != profile.expected_member_count:
            raise SourceDiscoveryError("archive member inventory is not the pinned 24-file set")
        for info in members:
            expanded_size += info.file_size
            compressed_member_size += info.compress_size
        if expanded_size > profile.archive_limits.maximum_expanded_bytes:
            raise ArchiveRejectedError("expanded archive size limit exceeded")
        if expanded_size and (
            compressed_member_size == 0
            or expanded_size / compressed_member_size
            > profile.archive_limits.maximum_expansion_ratio
        ):
            raise ArchiveRejectedError("archive expansion ratio limit exceeded")

        for info in sorted(members, key=lambda item: item.filename.encode()):
            member_digest = hashlib.sha256()
            member_dates: set[date] = set()
            member_routes: Counter[str] = Counter()
            member_event_types: Counter[str] = Counter()
            seen_units: set[bytes] = set()
            seen_semantic_events: set[bytes] = set()
            row_count = 0
            month = _member_month(info.filename)
            with source.open(info) as raw:
                reader = csv.DictReader(_decoded_lines(raw, member_digest))
                if tuple(reader.fieldnames or ()) != profile.schema_fields:
                    raise SourceDiscoveryError(
                        f"archive member schema drifted: {info.filename}: {reader.fieldnames}"
                    )
                for row_count, row in enumerate(reader, start=1):
                    if None in row or any(row[field] is None for field in profile.schema_fields):
                        raise SourceDiscoveryError(
                            f"malformed CSV row in {info.filename} at data row {row_count}"
                        )
                    try:
                        service_date = date.fromisoformat(row["service_date"])
                        direction_id = int(row["direction_id"])
                        stop_sequence = int(row["stop_sequence"])
                        event_time = int(row["event_time"])
                        event_time_sec = int(row["event_time_sec"])
                    except ValueError as error:
                        raise SourceDiscoveryError(
                            f"invalid typed value in {info.filename} at data row {row_count}"
                        ) from error
                    if (
                        service_date.year != profile.archive_year
                        or service_date.month != month
                        or direction_id not in (0, 1)
                        or stop_sequence < 0
                        or event_time <= 0
                        or event_time_sec < 0
                    ):
                        raise SourceDiscoveryError(
                            f"out-of-contract value in {info.filename} at data row {row_count}"
                        )
                    identity = (
                        row["route_id"],
                        row["trip_id"],
                        row["stop_id"],
                        row["vehicle_id"],
                    )
                    if any(not value for value in identity):
                        invalid_identity_rows += 1
                    if row["event_type"] not in profile.event_types:
                        raise SourceDiscoveryError(
                            f"unknown event type in {info.filename}: {row['event_type']}"
                        )
                    reconstructed_seconds = _service_seconds(event_time, service_date)
                    timestamp_delta = event_time_sec - reconstructed_seconds
                    timestamp_delta_counts[timestamp_delta] += 1
                    if timestamp_delta != 0:
                        timestamp_mismatches += 1
                        if len(timestamp_mismatch_examples) < 10:
                            timestamp_mismatch_examples.append(
                                {
                                    "member": info.filename,
                                    "row_number": row_count,
                                    "service_date": row["service_date"],
                                    "event_time": event_time,
                                    "event_time_sec": event_time_sec,
                                    "reconstructed_event_time_sec": reconstructed_seconds,
                                    "delta_seconds": timestamp_delta,
                                }
                            )
                    unit = _event_unit_digest(row)
                    if unit in seen_units:
                        repeated_event_units += 1
                        if len(repeated_event_unit_examples) < 10:
                            repeated_event_unit_examples.append(
                                {
                                    "member": info.filename,
                                    "row_number": row_count,
                                    "service_date": row["service_date"],
                                    "route_id": row["route_id"],
                                    "trip_id": row["trip_id"],
                                    "vehicle_id": row["vehicle_id"],
                                    "stop_id": row["stop_id"],
                                    "stop_sequence": stop_sequence,
                                    "event_type": row["event_type"],
                                    "event_time": event_time,
                                }
                            )
                    else:
                        seen_units.add(unit)
                    semantic_event = _semantic_event_digest(row)
                    if semantic_event in seen_semantic_events:
                        duplicate_semantic_events += 1
                        if len(duplicate_semantic_event_examples) < 10:
                            duplicate_semantic_event_examples.append(
                                {
                                    "member": info.filename,
                                    "row_number": row_count,
                                    "service_date": row["service_date"],
                                    "route_id": row["route_id"],
                                    "trip_id": row["trip_id"],
                                    "vehicle_id": row["vehicle_id"],
                                    "stop_id": row["stop_id"],
                                    "stop_sequence": stop_sequence,
                                    "event_type": row["event_type"],
                                    "event_time": event_time,
                                }
                            )
                    else:
                        seen_semantic_events.add(semantic_event)
                    member_dates.add(service_date)
                    member_routes[row["route_id"]] += 1
                    member_event_types[row["event_type"]] += 1
            member_reports.append(
                {
                    "member": info.filename,
                    "compressed_size_bytes": info.compress_size,
                    "expanded_size_bytes": info.file_size,
                    "sha256": member_digest.hexdigest(),
                    "row_count": row_count,
                    "first_service_date": min(member_dates).isoformat(),
                    "last_service_date": max(member_dates).isoformat(),
                    "route_counts": dict(sorted(member_routes.items())),
                    "event_type_counts": dict(sorted(member_event_types.items())),
                }
            )
            all_dates.update(member_dates)
            all_routes.update(member_routes)
            all_event_types.update(member_event_types)

    expected_dates = _expected_dates(profile.archive_year)
    selected_dates = deterministic_sample_dates(profile.archive_year, public_seed)
    checks = {
        "archive_bytes_pinned": True,
        "archive_limits_satisfied": True,
        "exact_member_inventory": len(member_reports) == profile.expected_member_count,
        "schema_stable_by_exact_header": True,
        "complete_service_date_inventory": all_dates == expected_dates,
        "sample_dates_available": set(selected_dates) <= all_dates,
        "identity_fields_complete": invalid_identity_rows == 0,
        "event_time_is_valid_epoch": True,
        "producer_service_seconds_drift_classified": (
            _producer_service_seconds_drift_is_classified(set(timestamp_delta_counts))
        ),
        "source_rows_have_stable_member_and_row_identity": True,
        "semantic_duplicates_quantified": duplicate_semantic_events == 0
        or bool(duplicate_semantic_event_examples),
        "expected_routes_only_and_present": set(all_routes) == set(profile.routes),
        "actual_and_prediction_events_distinguishable": (
            all(all_event_types[event_type] > 0 for event_type in profile.event_types)
        ),
        "direct_vehicle_position_stop_evidence_present": all_event_types["ARR"] > 0,
    }
    failing_checks = [name for name, passed in checks.items() if not passed]
    inventory_bytes = json.dumps(member_reports, sort_keys=True, separators=(",", ":")).encode()
    return {
        "status": "PASSED" if not failing_checks else "FAILED",
        "checks": checks,
        "failing_checks": failing_checks,
        "archive_sha256": archive_digest,
        "compressed_size_bytes": compressed_size,
        "expanded_size_bytes": expanded_size,
        "member_count": len(member_reports),
        "member_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "members": member_reports,
        "service_date_count": len(all_dates),
        "first_service_date": min(all_dates).isoformat(),
        "last_service_date": max(all_dates).isoformat(),
        "row_count": sum(report["row_count"] for report in member_reports),
        "route_counts": dict(sorted(all_routes.items())),
        "event_type_counts": dict(sorted(all_event_types.items())),
        "invalid_identity_rows": invalid_identity_rows,
        "timestamp_mismatches": timestamp_mismatches,
        "timestamp_delta_counts": {
            str(delta): count for delta, count in sorted(timestamp_delta_counts.items())
        },
        "timestamp_mismatch_examples": timestamp_mismatch_examples,
        "repeated_event_units": repeated_event_units,
        "repeated_event_unit_examples": repeated_event_unit_examples,
        "duplicate_semantic_events": duplicate_semantic_events,
        "duplicate_semantic_event_examples": duplicate_semantic_event_examples,
        "selected_sample_dates": [value.isoformat() for value in selected_dates],
        "schedule_boundary_samples_pending": True,
        "major_discontinuity_samples_pending": True,
    }


def _acceptance_seed(path: Path) -> str:
    loaded = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "acceptance charter")
    query = _mapping(loaded.get("query_generation"), "query_generation")
    return _string(query.get("public_seed"), "query_generation.public_seed")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def run_discovery(
    *,
    profile: SourceProfile,
    metadata_path: Path,
    archive_path: Path,
    acceptance_charter: Path,
    acquired_at_utc: datetime,
    runtime_manifest_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Validate the real source and write separate non-gate evidence artifacts."""

    metadata_body = metadata_path.read_bytes()
    metadata = validate_metadata(profile, metadata_body)
    public_seed = _acceptance_seed(acceptance_charter)
    archive = inspect_archive(profile, archive_path, public_seed)
    conservative_available = max(profile.expected_modified_at_utc, acquired_at_utc)
    manifest = {
        "qualification": "source-discovery-v1",
        "source_profile_version": profile.source_profile_version,
        "profile_sha256": sha256_file(profile.profile_path),
        "acceptance_charter_sha256": sha256_file(acceptance_charter),
        "metadata_sha256": hashlib.sha256(metadata_body).hexdigest(),
        "metadata_modified_at_utc": profile.expected_modified_at_utc.isoformat(),
        "acquisition_completed_at_utc": acquired_at_utc.isoformat(),
        "conservative_product_available_at_utc": conservative_available.isoformat(),
        "source": {
            "source_id": profile.source_id,
            "item_id": profile.item_id,
            "metadata_url": profile.metadata_url,
            "archive_url": profile.archive_url,
            "owner": metadata["owner"],
            "access": metadata["access"],
            "license": profile.license_identifier,
            "attribution": profile.attribution,
            "producer_repository": profile.producer_repository,
            "producer_commit": profile.producer_commit,
            "producer_file_hashes": dict(sorted(profile.producer_file_hashes.items())),
        },
        "archive": archive,
        "limitations": [
            "The public CSV omits the producer's feed/file timestamp.",
            "Historical events are label-only and unavailable as 2022 query-time features.",
            "ARR is the first observed VehiclePosition STOPPED_AT upper bound, "
            "not an exact physical arrival.",
            "PRA and PRD are prediction fallbacks and are forbidden as primary labels.",
            "Discovery passing does not satisfy the Milestone 0 acceptance gate.",
        ],
        "redistribution": {
            "raw_archive": profile.raw_archive_redistribution,
            "repository_policy": profile.project_artifact_policy,
        },
    }
    manifest_bytes = _canonical_json(manifest)
    runtime_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_manifest_path.write_bytes(manifest_bytes)
    report_checks = {
        "metadata_identity_pinned": True,
        "metadata_public_access_verified": metadata["access"] == profile.expected_access,
        "metadata_cc0_verified": metadata["licenseInfo"] == profile.expected_license,
        "archive_contract_passed": archive["status"] == "PASSED",
        "producer_semantics_pinned": len(profile.producer_file_hashes) == 3,
        "event_time_not_used_as_product_availability": conservative_available
        > datetime(profile.archive_year, 12, 31, tzinfo=UTC),
        "raw_archive_repository_policy_defined": bool(profile.project_artifact_policy),
    }
    report_failing = [name for name, passed in report_checks.items() if not passed]
    report = {
        "qualification": "source-discovery-v1",
        "status": "PASSED" if not report_failing else "FAILED",
        "milestone_0_accepted": False,
        "blocker_state": "PUBLIC_OFFICIAL_SOURCE_DISCOVERED_M0_AUDIT_PENDING",
        "checks": report_checks,
        "failing_checks": report_failing,
        "source_id": profile.source_id,
        "source_profile_version": profile.source_profile_version,
        "source_item_id": profile.item_id,
        "archive_sha256": archive["archive_sha256"],
        "archive_member_inventory_sha256": archive["member_inventory_sha256"],
        "metadata_sha256": manifest["metadata_sha256"],
        "runtime_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "service_date_count": archive["service_date_count"],
        "first_service_date": archive["first_service_date"],
        "last_service_date": archive["last_service_date"],
        "row_count": archive["row_count"],
        "event_type_counts": archive["event_type_counts"],
        "route_counts": archive["route_counts"],
        "producer_service_second_mismatches": archive["timestamp_mismatches"],
        "producer_service_second_delta_counts": archive["timestamp_delta_counts"],
        "repeated_event_units": archive["repeated_event_units"],
        "duplicate_semantic_events": archive["duplicate_semantic_events"],
        "source_limitations": manifest["limitations"],
        "license": profile.license_identifier,
        "attribution": profile.attribution,
        "raw_archive_committed": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(_canonical_json(report))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("configs/sources/mbta-rapid-transit-events-2022.yaml"),
    )
    parser.add_argument(
        "--acceptance-charter", type=Path, default=Path("configs/acceptance/v1.yaml")
    )
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--raw-root", type=Path, default=Path("data/raw/mbta-rapid-transit-events-2022")
    )
    parser.add_argument(
        "--runtime-manifest",
        type=Path,
        default=Path("artifacts/runtime/source-discovery/mbta-events-2022.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/reports/qualification/source-discovery-v1.json"),
    )
    parser.add_argument("--acquired-at-utc")
    return parser


def main() -> None:
    """Run the source-discovery workflow without modifying the M0 gate report."""

    args = _parser().parse_args()
    profile = load_source_profile(args.profile)
    if args.download and (args.metadata is not None or args.archive is not None):
        raise SystemExit("--download cannot be combined with --metadata or --archive")
    if not args.download and (args.metadata is None or args.archive is None):
        raise SystemExit("provide --download or both --metadata and --archive")
    if args.download:
        metadata_path = args.raw_root / "item-metadata.json"
        archive_path = args.raw_root / profile.expected_name
        metadata_body = _download_metadata(profile, metadata_path)
        validate_metadata(profile, metadata_body)
        acquired_at = _download_archive(profile, archive_path)
    else:
        metadata_path = args.metadata
        archive_path = args.archive
        if args.acquired_at_utc is None:
            raise SystemExit("--acquired-at-utc is required for local inputs")
        acquired_at = datetime.fromisoformat(args.acquired_at_utc)
        if acquired_at.tzinfo is None or acquired_at.utcoffset() != UTC.utcoffset(acquired_at):
            raise SystemExit("--acquired-at-utc must be timezone-aware UTC")
    report = run_discovery(
        profile=profile,
        metadata_path=metadata_path,
        archive_path=archive_path,
        acceptance_charter=args.acceptance_charter,
        acquired_at_utc=acquired_at,
        runtime_manifest_path=args.runtime_manifest,
        report_path=args.report,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "milestone_0_accepted": report["milestone_0_accepted"],
                "failing_checks": report["failing_checks"],
            },
            sort_keys=True,
        )
    )
    if report["status"] != "PASSED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
