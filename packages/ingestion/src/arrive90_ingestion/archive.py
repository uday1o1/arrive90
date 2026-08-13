"""Bounded and traversal-safe GTFS schedule archive extraction."""

from __future__ import annotations

import hashlib
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ArchiveRejectedError(ValueError):
    """Raised before extracting an unsafe or over-limit archive."""


@dataclass(frozen=True)
class ArchiveLimits:
    maximum_compressed_bytes: int = 512 * 1024 * 1024
    maximum_expanded_bytes: int = 8 * 1024 * 1024 * 1024
    maximum_expansion_ratio: float = 64.0

    def __post_init__(self) -> None:
        if (
            min(
                self.maximum_compressed_bytes,
                self.maximum_expanded_bytes,
                self.maximum_expansion_ratio,
            )
            <= 0
        ):
            raise ValueError("archive limits must be positive")


@dataclass(frozen=True)
class ExtractedMember:
    normalized_path: str
    size_bytes: int
    sha256: str


def _normalized_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ArchiveRejectedError("backslash archive member is ambiguous")
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts:
        raise ArchiveRejectedError("archive member escapes extraction root")
    normalized = PurePosixPath(*[part for part in member.parts if part not in ("", ".")])
    if not normalized.parts:
        raise ArchiveRejectedError("archive member has an empty normalized path")
    return normalized


def extract_zip(
    archive: Path, output_root: Path, limits: ArchiveLimits | None = None
) -> list[ExtractedMember]:
    """Validate every member, then extract regular files into a fresh root."""

    limits = limits or ArchiveLimits()
    if archive.stat().st_size > limits.maximum_compressed_bytes:
        raise ArchiveRejectedError("compressed archive size limit exceeded")
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise ArchiveRejectedError("extraction root must be a fresh directory")
    with zipfile.ZipFile(archive) as source:
        entries: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        names: set[str] = set()
        expanded = 0
        compressed = 0
        for info in source.infolist():
            normalized = _normalized_member(info.filename)
            normalized_text = normalized.as_posix()
            if normalized_text in names:
                raise ArchiveRejectedError("duplicate normalized archive path")
            names.add(normalized_text)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind == stat.S_IFLNK:
                raise ArchiveRejectedError("archive links are forbidden")
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ArchiveRejectedError("archive device or special file is forbidden")
            expanded += info.file_size
            compressed += info.compress_size
            entries.append((info, normalized))
        if expanded > limits.maximum_expanded_bytes:
            raise ArchiveRejectedError("expanded archive size limit exceeded")
        if expanded and (compressed == 0 or expanded / compressed > limits.maximum_expansion_ratio):
            raise ArchiveRejectedError("archive expansion ratio limit exceeded")

        output_root.mkdir(parents=True, exist_ok=True)
        extracted: list[ExtractedMember] = []
        for info, normalized in entries:
            destination = output_root.joinpath(*normalized.parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with source.open(info) as input_stream, destination.open("xb") as output_stream:
                while block := input_stream.read(1024 * 1024):
                    written += len(block)
                    if written > info.file_size or written > limits.maximum_expanded_bytes:
                        raise ArchiveRejectedError("archive member exceeded declared or total size")
                    digest.update(block)
                    output_stream.write(block)
            extracted.append(ExtractedMember(normalized.as_posix(), written, digest.hexdigest()))
    return sorted(extracted, key=lambda member: member.normalized_path.encode())
