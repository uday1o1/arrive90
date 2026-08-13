from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest
from arrive90_ingestion.archive import ArchiveLimits, ArchiveRejectedError, extract_zip


def _zip(path: Path, members: list[tuple[str, bytes, int | None]]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content, mode in members:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, content)
    return path


def test_extract_zip_returns_deterministic_member_hashes(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "feed.zip",
        [("stops.txt", b"stops", None), ("nested/trips.txt", b"trips", None)],
    )
    members = extract_zip(archive, tmp_path / "out", ArchiveLimits(maximum_expansion_ratio=100))
    assert [member.normalized_path for member in members] == ["nested/trips.txt", "stops.txt"]
    assert (tmp_path / "out" / "stops.txt").read_bytes() == b"stops"


@pytest.mark.parametrize(
    "name",
    ["../escape", "/absolute", "windows\\escape", "."],
)
def test_unsafe_member_paths_are_rejected(tmp_path: Path, name: str) -> None:
    archive = _zip(tmp_path / "bad.zip", [(name, b"data", None)])
    with pytest.raises(ArchiveRejectedError):
        extract_zip(archive, tmp_path / "out")


def test_links_devices_and_duplicate_normalized_paths_are_rejected(tmp_path: Path) -> None:
    link = _zip(tmp_path / "link.zip", [("link", b"target", stat.S_IFLNK | 0o777)])
    with pytest.raises(ArchiveRejectedError, match="links"):
        extract_zip(link, tmp_path / "link-out")
    device = _zip(tmp_path / "device.zip", [("device", b"", stat.S_IFCHR | 0o600)])
    with pytest.raises(ArchiveRejectedError, match="device"):
        extract_zip(device, tmp_path / "device-out")
    duplicate = _zip(tmp_path / "duplicate.zip", [("same", b"one", None), ("./same", b"two", None)])
    with pytest.raises(ArchiveRejectedError, match="duplicate"):
        extract_zip(duplicate, tmp_path / "duplicate-out")


def test_archive_size_ratio_and_fresh_root_limits(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "large.zip", [("large", b"0" * 1000, None)])
    with pytest.raises(ArchiveRejectedError, match="compressed archive"):
        extract_zip(archive, tmp_path / "compressed", ArchiveLimits(maximum_compressed_bytes=1))
    with pytest.raises(ArchiveRejectedError, match="expanded archive"):
        extract_zip(archive, tmp_path / "expanded", ArchiveLimits(maximum_expanded_bytes=10))
    with pytest.raises(ArchiveRejectedError, match="expansion ratio"):
        extract_zip(archive, tmp_path / "ratio", ArchiveLimits(maximum_expansion_ratio=1))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing").write_text("data", encoding="utf-8")
    with pytest.raises(ArchiveRejectedError, match="fresh directory"):
        extract_zip(archive, occupied)
    not_directory = tmp_path / "file"
    not_directory.write_text("data", encoding="utf-8")
    with pytest.raises(ArchiveRejectedError, match="fresh directory"):
        extract_zip(archive, not_directory)
