from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.request import Request

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from arrive90_ingestion.acquisition import (
    AcquisitionError,
    DownloadResult,
    acquisition_content_entry,
    download_resumable,
    expand_gzip_bounded,
    parquet_profile,
    schedule_derived_entry,
    select_schedule_version,
    sha256_file,
    sqlite_schema_fingerprint,
    write_acquisition_lock,
)

SOURCE_URL = "https://busobservatory-lake.s3.amazonaws.com/feeds/test.parquet"
ALLOWED_HOSTS = frozenset({"busobservatory-lake.s3.amazonaws.com"})
LAST_MODIFIED = datetime(2024, 5, 15, 12, 0, tzinfo=UTC)


class _Response(io.BytesIO):
    def __init__(
        self,
        body: bytes,
        *,
        status: int,
        url: str = SOURCE_URL,
        content_range: str | None = None,
    ) -> None:
        super().__init__(body)
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["ETag"] = '"etag-1"'
        self.headers["Last-Modified"] = "Wed, 15 May 2024 12:00:00 GMT"
        if content_range is not None:
            self.headers["Content-Range"] = content_range

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


class _Opener:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float) -> _Response:
        assert timeout > 0
        self.requests.append(request)
        return self.response


def _install_opener(monkeypatch: pytest.MonkeyPatch, opener: _Opener) -> None:
    monkeypatch.setattr(
        "arrive90_ingestion.acquisition.urllib.request.build_opener",
        lambda *_handlers: opener,
    )


def test_resumable_download_appends_only_a_valid_range_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"abcdef"
    destination = tmp_path / "object.parquet"
    destination.with_name("object.parquet.part").write_bytes(payload[:3])
    opener = _Opener(_Response(payload[3:], status=206, content_range="bytes 3-5/6"))
    _install_opener(monkeypatch, opener)

    result = download_resumable(
        SOURCE_URL,
        destination,
        allowed_hosts=ALLOWED_HOSTS,
        maximum_bytes=10,
        expected_size_bytes=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_etag="etag-1",
        expected_last_modified_at_utc=LAST_MODIFIED,
    )

    assert destination.read_bytes() == payload
    assert opener.requests[0].get_header("Range") == "bytes=3-"
    assert result.etag == "etag-1"
    assert result.last_modified_at_utc == LAST_MODIFIED


def test_download_restarts_when_server_ignores_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"new-body"
    destination = tmp_path / "object.parquet"
    destination.with_name("object.parquet.part").write_bytes(b"old")
    opener = _Opener(_Response(payload, status=200))
    _install_opener(monkeypatch, opener)

    result = download_resumable(
        SOURCE_URL,
        destination,
        allowed_hosts=ALLOWED_HOSTS,
        maximum_bytes=100,
        expected_size_bytes=len(payload),
    )

    assert destination.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()


def test_download_rejects_untrusted_oversized_and_wrong_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AcquisitionError, match="allow-list"):
        download_resumable(
            "https://example.com/object",
            tmp_path / "object",
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=10,
        )
    with pytest.raises(AcquisitionError, match="bounded size"):
        download_resumable(
            SOURCE_URL,
            tmp_path / "object",
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=5,
            expected_size_bytes=6,
        )

    destination = tmp_path / "wrong.parquet"
    opener = _Opener(_Response(b"wrong", status=200))
    _install_opener(monkeypatch, opener)
    with pytest.raises(AcquisitionError, match="SHA-256"):
        download_resumable(
            SOURCE_URL,
            destination,
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=10,
            expected_sha256="a" * 64,
        )
    assert not destination.with_name("wrong.parquet.part").exists()


def test_existing_verified_download_is_reused_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "existing.parquet"
    destination.write_bytes(b"verified")
    monkeypatch.setattr(
        "arrive90_ingestion.acquisition.urllib.request.build_opener",
        lambda *_handlers: pytest.fail("verified file must not open the network"),
    )
    result = download_resumable(
        SOURCE_URL,
        destination,
        allowed_hosts=ALLOWED_HOSTS,
        maximum_bytes=100,
        expected_size_bytes=8,
        expected_sha256=sha256_file(destination),
        expected_etag="etag-1",
        expected_last_modified_at_utc=LAST_MODIFIED,
    )
    assert result.size_bytes == 8
    assert result.downloaded_at_utc.tzinfo is UTC


def test_existing_download_rejects_wrong_size_and_digest(tmp_path: Path) -> None:
    destination = tmp_path / "existing.parquet"
    destination.write_bytes(b"bytes")
    with pytest.raises(AcquisitionError, match="size"):
        download_resumable(
            SOURCE_URL,
            destination,
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
            expected_size_bytes=6,
        )
    with pytest.raises(AcquisitionError, match="SHA-256"):
        download_resumable(
            SOURCE_URL,
            destination,
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
            expected_sha256="a" * 64,
        )


def test_download_validates_expected_hash_and_response_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AcquisitionError, match="lowercase hexadecimal"):
        download_resumable(
            SOURCE_URL,
            tmp_path / "invalid",
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
            expected_sha256="bad",
        )

    opener = _Opener(_Response(b"body", status=200))
    _install_opener(monkeypatch, opener)
    with pytest.raises(AcquisitionError, match="ETag"):
        download_resumable(
            SOURCE_URL,
            tmp_path / "etag",
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
            expected_etag="different",
        )
    opener.response = _Response(b"body", status=200)
    with pytest.raises(AcquisitionError, match="Last-Modified"):
        download_resumable(
            SOURCE_URL,
            tmp_path / "modified",
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
            expected_last_modified_at_utc=datetime(2024, 5, 16, tzinfo=UTC),
        )


def test_download_rejects_invalid_resume_status_range_and_final_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "range.parquet"
    destination.with_name("range.parquet.part").write_bytes(b"abc")
    opener = _Opener(_Response(b"def", status=206, content_range="bytes 2-5/6"))
    _install_opener(monkeypatch, opener)
    with pytest.raises(AcquisitionError, match="Content-Range"):
        download_resumable(
            SOURCE_URL,
            destination,
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
        )

    destination.with_name("range.parquet.part").unlink()
    opener.response = _Response(b"body", status=201)
    with pytest.raises(AcquisitionError, match="unexpected HTTP status"):
        download_resumable(
            SOURCE_URL,
            destination,
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
        )

    opener.response = _Response(b"short", status=200)
    with pytest.raises(AcquisitionError, match="downloaded size"):
        download_resumable(
            SOURCE_URL,
            destination,
            allowed_hosts=ALLOWED_HOSTS,
            maximum_bytes=100,
            expected_size_bytes=6,
        )


def test_parquet_profile_hashes_name_type_and_nullability(tmp_path: Path) -> None:
    path = tmp_path / "sample.parquet"
    pq.write_table(pa.table({"value": pa.array([1, 2], type=pa.int64())}), path)
    first = parquet_profile(path)
    second = parquet_profile(path)
    assert first == second
    assert first.row_count == 2
    assert first.columns == (("value", "int64", True),)
    assert len(first.schema_fingerprint) == 64


def _schedule_database(path: Path, *, feed_version: str | None = None) -> None:
    version = feed_version or "MBTA, 2024-05-10T12:00:00+00:00, v1"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE feed_info "
            "(feed_version TEXT, gtfs_active_date INTEGER, gtfs_end_date INTEGER)"
        )
        connection.execute(
            "INSERT INTO feed_info VALUES (?, 20240501, 20240531)",
            (version,),
        )
        connection.execute("CREATE INDEX feed_info_dates ON feed_info(gtfs_active_date)")


def test_bounded_gzip_expansion_and_sqlite_version_selection(tmp_path: Path) -> None:
    database_body = tmp_path / "source.db"
    _schedule_database(database_body)
    compressed = tmp_path / "source.db.gz"
    with gzip.open(compressed, "wb") as stream:
        stream.write(database_body.read_bytes())
    expanded = tmp_path / "expanded.db"

    size, digest = expand_gzip_bounded(compressed, expanded)
    assert size == expanded.stat().st_size
    assert digest == sha256_file(expanded)
    assert expand_gzip_bounded(compressed, expanded) == (size, digest)
    schema_fingerprint = sqlite_schema_fingerprint(expanded)
    assert len(schema_fingerprint) == 64

    selected = select_schedule_version(
        expanded,
        service_date=date(2024, 5, 15),
        cutoff_utc=datetime(2024, 5, 15, 13, 0, tzinfo=UTC),
        expanded_database_sha256=digest,
    )
    assert selected.active_start == date(2024, 5, 1)
    assert selected.active_end == date(2024, 5, 31)
    assert selected.published_at_utc == datetime(2024, 5, 10, 12, tzinfo=UTC)
    assert len(selected.schedule_version_id) == 64


def test_gzip_expansion_enforces_output_limit(tmp_path: Path) -> None:
    compressed = tmp_path / "bomb.gz"
    with gzip.open(compressed, "wb") as stream:
        stream.write(b"0123456789")
    with pytest.raises(AcquisitionError, match="bounded size"):
        expand_gzip_bounded(compressed, tmp_path / "output", maximum_output_bytes=5)
    with pytest.raises(AcquisitionError, match="must be positive"):
        expand_gzip_bounded(compressed, tmp_path / "invalid", maximum_output_bytes=0)
    invalid = tmp_path / "invalid.gz"
    invalid.write_bytes(b"not-gzip")
    with pytest.raises(AcquisitionError, match="complete gzip"):
        expand_gzip_bounded(invalid, tmp_path / "invalid-output")


def test_schedule_selector_rejects_future_conflicting_and_invalid_versions(
    tmp_path: Path,
) -> None:
    future = tmp_path / "future.db"
    _schedule_database(future)
    digest = sha256_file(future)
    with pytest.raises(AcquisitionError, match="published after"):
        select_schedule_version(
            future,
            service_date=date(2024, 5, 15),
            cutoff_utc=datetime(2024, 5, 9, tzinfo=UTC),
            expanded_database_sha256=digest,
        )

    with sqlite3.connect(future) as connection:
        connection.execute(
            "INSERT INTO feed_info VALUES "
            "('MBTA, 2024-05-11T12:00:00+00:00, v2', 20240501, 20240531)"
        )
    with pytest.raises(AcquisitionError, match="multiple active"):
        select_schedule_version(
            future,
            service_date=date(2024, 5, 15),
            cutoff_utc=datetime(2024, 5, 15, tzinfo=UTC),
            expanded_database_sha256=sha256_file(future),
        )

    invalid = tmp_path / "invalid.db"
    _schedule_database(invalid, feed_version="not-versioned")
    with pytest.raises(AcquisitionError, match="publication timestamp"):
        select_schedule_version(
            invalid,
            service_date=date(2024, 5, 15),
            cutoff_utc=datetime(2024, 5, 15, tzinfo=UTC),
            expanded_database_sha256=sha256_file(invalid),
        )

    with pytest.raises(AcquisitionError, match="no active"):
        select_schedule_version(
            invalid,
            service_date=date(2024, 6, 1),
            cutoff_utc=datetime(2024, 6, 1, tzinfo=UTC),
            expanded_database_sha256=sha256_file(invalid),
        )
    missing = tmp_path / "missing.db"
    with sqlite3.connect(missing) as connection:
        connection.execute("CREATE TABLE other (value TEXT)")
    with pytest.raises(AcquisitionError, match="required feed_info"):
        select_schedule_version(
            missing,
            service_date=date(2024, 5, 15),
            cutoff_utc=datetime(2024, 5, 15, tzinfo=UTC),
            expanded_database_sha256=sha256_file(missing),
        )


def test_acquisition_and_derived_entries_write_an_immutable_lock(tmp_path: Path) -> None:
    downloaded = tmp_path / "sample.parquet"
    downloaded.write_bytes(b"sample")
    result = DownloadResult(
        path=downloaded,
        size_bytes=6,
        sha256=sha256_file(downloaded),
        etag=None,
        last_modified_at_utc=None,
        downloaded_at_utc=datetime(2024, 5, 15, tzinfo=UTC),
    )
    content = acquisition_content_entry(
        result,
        source_object_key="sample.parquet",
        source_url=SOURCE_URL,
        schema_fingerprint="b" * 64,
        row_count=2,
        parser_version="test-v1",
    )
    derived = schedule_derived_entry(
        compressed_sha256="c" * 64,
        expanded_path=downloaded,
        expanded_sha256=result.sha256,
        schema_fingerprint="d" * 64,
    )
    lock = tmp_path / "lock.json"
    first = write_acquisition_lock(lock, content_entries=[content], derived_entries=[derived])
    second = write_acquisition_lock(lock, content_entries=[content], derived_entries=[derived])
    assert first == second == hashlib.sha256(lock.read_bytes()).hexdigest()
    payload: dict[str, Any] = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["content_entries"][0]["sha256"] == result.sha256

    changed = acquisition_content_entry(
        result,
        source_object_key="different.parquet",
        source_url=SOURCE_URL,
        schema_fingerprint="b" * 64,
        row_count=2,
        parser_version="test-v1",
    )
    with pytest.raises(AcquisitionError, match="different bytes"):
        write_acquisition_lock(lock, content_entries=[changed], derived_entries=[derived])
