"""Bounded, resumable acquisition and schedule-archive verification."""

from __future__ import annotations

import gzip
import hashlib
import os
import platform
import re
import sqlite3
import tempfile
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from arrive90_data_contracts.gates import DEFAULT_ACCEPTANCE_VERSION
from arrive90_data_contracts.realtime import require_utc
from arrive90_data_contracts.source import AcquisitionContentEntry, DerivedArtifactEntry

from arrive90_ingestion.historical import canonical_json_bytes

READ_BLOCK_BYTES = 1024 * 1024
MAX_SCHEDULE_DATABASE_BYTES = 4 * 1024 * 1024 * 1024
DOWNLOAD_USER_AGENT = "arrive90/travel-time-v1"
PARQUET_PARSER_VERSION = "arrive90-pyarrow-parquet-v1"
SCHEDULE_PARSER_VERSION = "arrive90-sqlite-schedule-v1"
GZIP_EXPANSION_VERSION = f"arrive90-python-{platform.python_version()}-gzip-v1"


class AcquisitionError(ValueError):
    """Raised when public bytes or derived artifacts fail closed."""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Verified facts about one immutable downloaded file."""

    path: Path
    size_bytes: int
    sha256: str
    etag: str | None
    last_modified_at_utc: datetime | None
    downloaded_at_utc: datetime


@dataclass(frozen=True, slots=True)
class HttpObjectMetadata:
    """Stable HTTP object metadata observed before a bounded download."""

    size_bytes: int
    etag: str | None
    last_modified_at_utc: datetime | None


@dataclass(frozen=True, slots=True)
class ParquetProfile:
    """Stable physical facts required before normalization."""

    row_count: int
    schema_fingerprint: str
    columns: tuple[tuple[str, str, bool], ...]


@dataclass(frozen=True, slots=True)
class ScheduleVersion:
    """One uniquely selected active schedule archive version."""

    feed_version: str
    published_at_utc: datetime
    active_start: date
    active_end: date
    schedule_version_id: str


def sha256_file(path: Path) -> str:
    """Hash one file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(READ_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _validate_source_url(url: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise AcquisitionError("source URL is outside the pinned HTTPS host allow-list")


class _AllowListedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_source_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().removeprefix("W/").strip('"')
    return normalized or None


def _http_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    from email.utils import parsedate_to_datetime

    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        raise AcquisitionError("HTTP Last-Modified timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def fetch_http_object_metadata(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    maximum_bytes: int,
    timeout_seconds: float = 60,
) -> HttpObjectMetadata:
    """Read bounded immutable-object metadata with an allow-listed HTTPS HEAD request."""

    _validate_source_url(url, allowed_hosts)
    if maximum_bytes <= 0:
        raise AcquisitionError("maximum metadata object size must be positive")
    request = urllib.request.Request(  # noqa: S310 - URL allow-listed.
        url,
        headers={"User-Agent": DOWNLOAD_USER_AGENT},
        method="HEAD",
    )
    opener = urllib.request.build_opener(_AllowListedRedirectHandler(allowed_hosts))
    with opener.open(request, timeout=timeout_seconds) as response:
        _validate_source_url(response.geturl(), allowed_hosts)
        if response.getcode() != 200:
            raise AcquisitionError(f"unexpected HTTP status for metadata: {response.getcode()}")
        raw_size = response.headers.get("Content-Length")
        try:
            size = int(raw_size) if raw_size is not None else 0
        except ValueError as error:
            raise AcquisitionError("response Content-Length must be an integer") from error
        if not 0 < size <= maximum_bytes:
            raise AcquisitionError("response Content-Length is outside the bounded size limit")
        return HttpObjectMetadata(
            size_bytes=size,
            etag=_normalize_etag(response.headers.get("ETag")),
            last_modified_at_utc=_http_timestamp(response.headers.get("Last-Modified")),
        )


def _existing_download(
    destination: Path,
    *,
    expected_size_bytes: int | None,
    expected_sha256: str | None,
    expected_etag: str | None,
    expected_last_modified_at_utc: datetime | None,
) -> DownloadResult | None:
    if not destination.exists():
        return None
    size = destination.stat().st_size
    digest = sha256_file(destination)
    if expected_size_bytes is not None and size != expected_size_bytes:
        raise AcquisitionError("existing download size does not match the acquired-content lock")
    if expected_sha256 is not None and digest != expected_sha256:
        raise AcquisitionError("existing download SHA-256 does not match the acquired-content lock")
    downloaded_at = datetime.fromtimestamp(destination.stat().st_mtime, tz=UTC)
    return DownloadResult(
        path=destination,
        size_bytes=size,
        sha256=digest,
        etag=_normalize_etag(expected_etag),
        last_modified_at_utc=expected_last_modified_at_utc,
        downloaded_at_utc=downloaded_at,
    )


def download_resumable(
    url: str,
    destination: Path,
    *,
    allowed_hosts: frozenset[str],
    maximum_bytes: int,
    expected_size_bytes: int | None = None,
    expected_sha256: str | None = None,
    expected_etag: str | None = None,
    expected_last_modified_at_utc: datetime | None = None,
    timeout_seconds: float = 60,
) -> DownloadResult:
    """Resume one exact public HTTPS object and atomically publish verified bytes."""

    _validate_source_url(url, allowed_hosts)
    if maximum_bytes <= 0:
        raise AcquisitionError("maximum download size must be positive")
    if expected_size_bytes is not None and not 0 < expected_size_bytes <= maximum_bytes:
        raise AcquisitionError("expected download size is outside the bounded size limit")
    if expected_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise AcquisitionError("expected download SHA-256 must be lowercase hexadecimal")
    if expected_last_modified_at_utc is not None:
        require_utc(expected_last_modified_at_utc, "expected_last_modified_at_utc")
    existing = _existing_download(
        destination,
        expected_size_bytes=expected_size_bytes,
        expected_sha256=expected_sha256,
        expected_etag=expected_etag,
        expected_last_modified_at_utc=expected_last_modified_at_utc,
    )
    if existing is not None:
        return existing

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > maximum_bytes or (expected_size_bytes is not None and offset > expected_size_bytes):
        raise AcquisitionError("partial download exceeds the bounded size limit")
    headers = {"User-Agent": DOWNLOAD_USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - URL allow-listed.
    opener = urllib.request.build_opener(_AllowListedRedirectHandler(allowed_hosts))
    with opener.open(request, timeout=timeout_seconds) as response:
        _validate_source_url(response.geturl(), allowed_hosts)
        response_etag = _normalize_etag(response.headers.get("ETag"))
        if expected_etag is not None and response_etag != _normalize_etag(expected_etag):
            raise AcquisitionError("response ETag does not match the pinned value")
        response_last_modified = _http_timestamp(response.headers.get("Last-Modified"))
        if (
            expected_last_modified_at_utc is not None
            and response_last_modified != expected_last_modified_at_utc
        ):
            raise AcquisitionError("response Last-Modified does not match the pinned value")
        status = response.getcode()
        content_range = response.headers.get("Content-Range")
        if offset and status == 206:
            if content_range is None or not content_range.startswith(f"bytes {offset}-"):
                raise AcquisitionError("resumed response has an invalid Content-Range")
            mode = "ab"
        elif status == 200:
            offset = 0
            mode = "wb"
        else:
            raise AcquisitionError(f"unexpected HTTP status for download: {status}")

        total = offset
        with partial.open(mode) as output:
            while block := response.read(READ_BLOCK_BYTES):
                total += len(block)
                if total > maximum_bytes:
                    raise AcquisitionError("download exceeded the bounded size limit")
                output.write(block)
            output.flush()
            os.fsync(output.fileno())

    if expected_size_bytes is not None and total != expected_size_bytes:
        raise AcquisitionError("downloaded size does not match the pinned value")
    digest = sha256_file(partial)
    if expected_sha256 is not None and digest != expected_sha256:
        partial.unlink(missing_ok=True)
        raise AcquisitionError("downloaded SHA-256 does not match the pinned value")
    os.replace(partial, destination)
    downloaded_at = datetime.now(tz=UTC)
    os.utime(destination, (downloaded_at.timestamp(), downloaded_at.timestamp()))
    return DownloadResult(
        path=destination,
        size_bytes=total,
        sha256=digest,
        etag=response_etag,
        last_modified_at_utc=response_last_modified,
        downloaded_at_utc=downloaded_at,
    )


def parquet_profile(path: Path) -> ParquetProfile:
    """Fingerprint the physical Parquet schema without reading its row payload."""

    parquet = pq.ParquetFile(path)
    columns = tuple((field.name, str(field.type), field.nullable) for field in parquet.schema_arrow)
    fingerprint = hashlib.sha256(canonical_json_bytes(columns)).hexdigest()
    return ParquetProfile(
        row_count=parquet.metadata.num_rows,
        schema_fingerprint=fingerprint,
        columns=columns,
    )


def expand_gzip_bounded(
    source: Path,
    destination: Path,
    *,
    maximum_output_bytes: int = MAX_SCHEDULE_DATABASE_BYTES,
) -> tuple[int, str]:
    """Expand a gzip payload into a fresh immutable path under a hard byte limit."""

    if maximum_output_bytes <= 0:
        raise AcquisitionError("maximum expanded size must be positive")
    if destination.exists():
        return destination.stat().st_size, sha256_file(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="schedule-expand-", dir=destination.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as output, gzip.open(source, "rb") as compressed:
            while block := compressed.read(READ_BLOCK_BYTES):
                total += len(block)
                if total > maximum_output_bytes:
                    raise AcquisitionError("expanded schedule exceeds the bounded size limit")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except (gzip.BadGzipFile, EOFError) as error:
        raise AcquisitionError("schedule archive is not a complete gzip stream") from error
    finally:
        temporary.unlink(missing_ok=True)
    return total, digest.hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def sqlite_schema_fingerprint(path: Path) -> str:
    """Hash the complete canonical noninternal SQLite schema."""

    with _connect_read_only(path) as connection:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    schema = [tuple(row) for row in rows]
    schema.sort(
        key=lambda row: tuple(b"" if value is None else str(value).encode("utf-8") for value in row)
    )
    return hashlib.sha256(canonical_json_bytes(schema)).hexdigest()


def _parse_feed_version_publication(value: str) -> datetime:
    parts = value.split(", ")
    if len(parts) < 3:
        raise AcquisitionError(f"feed_version has no publication timestamp: {value}")
    try:
        published = datetime.fromisoformat(parts[-2].replace("Z", "+00:00"))
    except ValueError as error:
        raise AcquisitionError(f"feed_version publication timestamp is invalid: {value}") from error
    try:
        require_utc(published, "feed_version publication timestamp")
    except ValueError as error:
        raise AcquisitionError(str(error)) from error
    return published


def _schedule_date(value: object) -> date:
    text = str(value)
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as error:
        raise AcquisitionError(f"invalid schedule active date: {text}") from error


def select_schedule_version(
    database: Path,
    *,
    service_date: date,
    cutoff_utc: datetime,
    expanded_database_sha256: str,
) -> ScheduleVersion:
    """Select exactly one active schedule published no later than the observation cutoff."""

    require_utc(cutoff_utc, "cutoff_utc")
    service_key = int(service_date.strftime("%Y%m%d"))
    with _connect_read_only(database) as connection:
        try:
            rows = connection.execute(
                "SELECT feed_version, gtfs_active_date, gtfs_end_date FROM feed_info "
                "WHERE gtfs_active_date <= ? AND gtfs_end_date >= ?",
                (service_key, service_key),
            ).fetchall()
        except sqlite3.Error as error:
            raise AcquisitionError(
                "schedule archive does not expose the required feed_info schema"
            ) from error
    if not rows:
        raise AcquisitionError("no active schedule version matches the service date")
    if len(rows) != 1:
        raise AcquisitionError("multiple active schedule versions match the service date")
    row = rows[0]
    feed_version = str(row["feed_version"])
    published_at = _parse_feed_version_publication(feed_version)
    if published_at > cutoff_utc:
        raise AcquisitionError("active schedule version was published after the observation cutoff")
    active_start = _schedule_date(row["gtfs_active_date"])
    active_end = _schedule_date(row["gtfs_end_date"])
    version_payload = {
        "active_end": active_end,
        "active_start": active_start,
        "expanded_database_sha256": expanded_database_sha256,
        "feed_version": feed_version,
        "published_at_utc": published_at,
    }
    return ScheduleVersion(
        feed_version=feed_version,
        published_at_utc=published_at,
        active_start=active_start,
        active_end=active_end,
        schedule_version_id=hashlib.sha256(canonical_json_bytes(version_payload)).hexdigest(),
    )


def acquisition_content_entry(
    result: DownloadResult,
    *,
    source_object_key: str,
    source_url: str,
    schema_fingerprint: str,
    row_count: int,
    parser_version: str,
) -> AcquisitionContentEntry:
    """Bind verified download facts to the shared acquired-content contract."""

    return AcquisitionContentEntry(
        source_object_key=source_object_key,
        source_url=source_url,
        response_size_bytes=result.size_bytes,
        etag=result.etag,
        last_modified_at_utc=result.last_modified_at_utc,
        downloaded_at_utc=result.downloaded_at_utc,
        sha256=result.sha256,
        schema_fingerprint=schema_fingerprint,
        row_count=row_count,
        parser_version=parser_version,
    )


def schedule_derived_entry(
    *,
    compressed_sha256: str,
    expanded_path: Path,
    expanded_sha256: str,
    schema_fingerprint: str,
) -> DerivedArtifactEntry:
    """Bind the deterministic standard-library gzip expansion to its source bytes."""

    return DerivedArtifactEntry(
        artifact_id="mbta-gtfs-archive-2024-expanded-sqlite",
        source_content_sha256=compressed_sha256,
        transformation_name="gzip-expand",
        transformation_version=GZIP_EXPANSION_VERSION,
        transformation_parameters=(),
        output_size_bytes=expanded_path.stat().st_size,
        output_sha256=expanded_sha256,
        schema_fingerprint=schema_fingerprint,
    )


def write_acquisition_lock(
    path: Path,
    *,
    content_entries: Iterable[AcquisitionContentEntry],
    derived_entries: Iterable[DerivedArtifactEntry],
) -> str:
    """Write one immutable canonical acquisition lock and return its SHA-256."""

    payload = {
        "acceptance_version": DEFAULT_ACCEPTANCE_VERSION,
        "content_entries": [asdict(entry) for entry in content_entries],
        "derived_entries": [asdict(entry) for entry in derived_entries],
    }
    body = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise AcquisitionError(f"immutable acquisition lock has different bytes: {path}")
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix="acquisition-lock-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(body).hexdigest()
