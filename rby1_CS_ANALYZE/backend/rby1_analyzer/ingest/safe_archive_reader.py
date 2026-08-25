from __future__ import annotations

import gzip
import io
import re
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator

from .classifier import is_supported_member
from .quotas import Quotas

_SAFE_PAX_HEADERS = frozenset({"mtime"})


class ArchiveViolation(ValueError):
    def __init__(self, code: str, member: str | None = None):
        super().__init__(f"{code}: {member or ''}".rstrip())
        self.code = code
        self.member = member


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    stream: BinaryIO
    compressed_size: int
    size_hint: int
    provenance: tuple[str, ...]


class _CountingReader(io.RawIOBase):
    def __init__(
        self,
        raw: BinaryIO,
        cancel_check: Callable[[], None] | None = None,
        cancel_interval: int = 10 * 1024 * 1024,
        progress_callback: Callable[[int], None] | None = None,
        progress_interval: int = 1024 * 1024,
        max_count: int | None = None,
        limit_code: str = "expansion_budget",
    ):
        self.raw = raw
        self.count = 0
        self.cancel_check = cancel_check
        self.cancel_interval = cancel_interval
        self.next_cancel_check = cancel_interval
        self.progress_callback = progress_callback
        self.progress_interval = progress_interval
        self.next_progress = progress_interval
        self.max_count = max_count
        self.limit_code = limit_code

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.count += len(data)
        if self.max_count is not None and self.count > self.max_count:
            raise ArchiveViolation(self.limit_code)
        if self.cancel_check is not None and self.count >= self.next_cancel_check:
            self.cancel_check()
            self.next_cancel_check = self.count + self.cancel_interval
        if self.progress_callback is not None and self.count >= self.next_progress:
            self.progress_callback(self.count)
            self.next_progress = self.count + self.progress_interval
        return data

    def readinto(self, buffer: bytearray) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def seekable(self) -> bool:
        return self.raw.seekable()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.raw.seek(offset, whence)

    def tell(self) -> int:
        return self.raw.tell()


class _BoundedMemberReader(io.RawIOBase):
    def __init__(
        self,
        raw: BinaryIO,
        name: str,
        compressed_size: int | None,
        expanded_total: list[int],
        *,
        max_member: int,
        max_expanded: int,
        member_ratio: float,
        cancel_check: Callable[[], None] | None,
        cancel_interval: int,
        progress_callback: Callable[[int], None] | None = None,
        progress_interval: int = 1024 * 1024,
    ):
        self.raw = raw
        self.name = name
        self.compressed_size = compressed_size
        self.expanded_total = expanded_total
        self.max_member = max_member
        self.max_expanded = max_expanded
        self.member_ratio = member_ratio
        self.cancel_check = cancel_check
        self.cancel_interval = cancel_interval
        self.count = 0
        self.next_cancel_check = cancel_interval
        self.progress_callback = progress_callback
        self.progress_interval = progress_interval
        self.next_progress = progress_interval

    def readable(self) -> bool:
        return True

    def _account(self, data: bytes) -> bytes:
        length = len(data)
        if not length:
            return data
        self.count += length
        self.expanded_total[0] += length
        if self.cancel_check is not None and self.count >= self.next_cancel_check:
            self.cancel_check()
            self.next_cancel_check = self.count + self.cancel_interval
        if self.progress_callback is not None and self.count >= self.next_progress:
            self.progress_callback(self.count)
            self.next_progress = self.count + self.progress_interval
        if self.count > self.max_member:
            raise ArchiveViolation("member_too_large", self.name)
        if self.expanded_total[0] > self.max_expanded:
            raise ArchiveViolation("expansion_budget", self.name)
        if self.compressed_size == 0:
            raise ArchiveViolation("member_ratio", self.name)
        if (
            self.compressed_size is not None
            and self.count / self.compressed_size > self.member_ratio
        ):
            raise ArchiveViolation("member_ratio", self.name)
        return data

    def read(self, size: int = -1) -> bytes:
        return self._account(self.raw.read(size))

    def readline(self, size: int = -1) -> bytes:
        return self._account(self.raw.readline(size))

    def readinto(self, buffer: bytearray) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


class SafeArchiveReader:
                                                                                    

    def __init__(
        self,
        *,
        max_expanded: int | None = None,
        max_member: int | None = None,
        max_members: int = 10_000,
        member_ratio: float = 100.0,
        aggregate_ratio: float = 50.0,
        chunk_size: int = 1 << 20,
        cancel_check: Callable[[], None] | None = None,
        cancel_interval: int = 10 * 1024 * 1024,
        progress_callback: Callable[[int], None] | None = None,
        progress_interval: int = 1024 * 1024,
    ):
        quotas = Quotas()
        self.max_expanded = quotas.expanded_import if max_expanded is None else max_expanded
        self.max_member = quotas.member_size if max_member is None else max_member
        self.max_members = max_members
        self.member_ratio = member_ratio
        self.aggregate_ratio = aggregate_ratio
        self.chunk_size = chunk_size
        self.cancel_check = cancel_check
        self.cancel_interval = cancel_interval
        self.progress_callback = progress_callback
        self.progress_interval = progress_interval
        self.warnings: list[ArchiveViolation] = []

    @staticmethod
    def normalize_name(name: str, seen: set[str]) -> str:
        name = unicodedata.normalize("NFC", name.replace("\\", "/"))
        if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise ArchiveViolation("unsafe_path", name)
        parts = PurePosixPath(name).parts
        if any(part in ("", ".", "..") for part in parts):
            raise ArchiveViolation("unsafe_path", name)
        normalized = PurePosixPath(*parts).as_posix()
        if len(normalized.encode("utf-8")) > 512:
            raise ArchiveViolation("path_too_long", name)
        key = normalized.casefold()
        if key in seen:
            raise ArchiveViolation("normalized_collision", name)
        seen.add(key)
        return normalized

    def _bounded(
        self,
        stream: BinaryIO,
        name: str,
        compressed_size: int | None,
        total: list[int],
        progress_callback: Callable[[int], None] | None = None,
    ) -> _BoundedMemberReader:
        return _BoundedMemberReader(
            stream,
            name,
            compressed_size,
            total,
            max_member=self.max_member,
            max_expanded=self.max_expanded,
            member_ratio=self.member_ratio,
            cancel_check=self.cancel_check,
            cancel_interval=self.cancel_interval,
            progress_callback=progress_callback,
            progress_interval=self.progress_interval,
        )

    def _drain(self, stream: BinaryIO) -> None:
        while stream.read(self.chunk_size):
            pass

    def read(self, path: Path, *, source_name: str | None = None) -> Iterator[ArchiveMember]:
        self.warnings.clear()
        archive_name = source_name or path.name
        lower = archive_name.lower()
        if lower.endswith(".zip"):
            yield from self._zip(path, archive_name)
        elif lower.endswith((".tar.gz", ".tgz")):
            yield from self._targz(path, archive_name)
        elif lower.endswith(".tar"):
            yield from self._tar(path, archive_name)
        elif lower.endswith(".gz"):
            yield from self._gzip(path, archive_name)
        else:
            raise ArchiveViolation("unsupported_archive", archive_name)

    def _zip(self, path: Path, archive_name: str) -> Iterator[ArchiveMember]:
        seen: set[str] = set()
        total = [0]
        compressed_total = 0
        completed_work = 0
        count = 0
        archive_size = path.stat().st_size
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            work_total = sum(max(info.compress_size, 1) for info in infos if not info.is_dir()) or 1
            for info in infos:
                if self.cancel_check is not None:
                    self.cancel_check()
                if info.is_dir():
                    continue
                member_work = max(info.compress_size, 1)

                def report_member(expanded: int) -> None:
                    if self.progress_callback is None:
                        return
                    fraction = min(1.0, expanded / max(info.file_size, 1))
                    estimated_work = completed_work + int(member_work * fraction)
                    self.progress_callback(min(archive_size, archive_size * estimated_work // work_total))

                def finish_member() -> None:
                    nonlocal completed_work
                    completed_work += member_work
                    if self.progress_callback is not None:
                        self.progress_callback(
                            min(archive_size, archive_size * completed_work // work_total)
                        )

                count += 1
                if count > self.max_members:
                    raise ArchiveViolation("member_count")
                try:
                    name = self.normalize_name(info.filename, seen)
                    if info.flag_bits & 1:
                        raise ArchiveViolation("encrypted_zip", name)
                    if info.compress_type not in {
                        zipfile.ZIP_STORED,
                        zipfile.ZIP_DEFLATED,
                        zipfile.ZIP_BZIP2,
                        zipfile.ZIP_LZMA,
                    }:
                        raise ArchiveViolation("unsupported_method", name)
                    if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                        raise ArchiveViolation("link", name)
                except ArchiveViolation as warning:
                    self.warnings.append(warning)
                    finish_member()
                    continue
                if not is_supported_member(name):
                    self.warnings.append(ArchiveViolation("unsupported_member", name))
                    finish_member()
                    continue
                with archive.open(info) as stream:
                    bounded = self._bounded(
                        stream,
                        name,
                        info.compress_size,
                        total,
                        progress_callback=report_member,
                    )
                    yield ArchiveMember(
                        name,
                        bounded,
                        info.compress_size,
                        info.file_size,
                        (archive_name, name),
                    )
                    self._drain(bounded)
                finish_member()
                compressed_total += info.compress_size
                if compressed_total == 0 and total[0]:
                    raise ArchiveViolation("aggregate_ratio")
                if compressed_total and total[0] / compressed_total > self.aggregate_ratio:
                    raise ArchiveViolation("aggregate_ratio")
        if self.progress_callback is not None:
            self.progress_callback(archive_size)

    def _gzip(self, path: Path, archive_name: str) -> Iterator[ArchiveMember]:
        total = [0]
        compressed_size = path.stat().st_size
        with path.open("rb") as raw:
            counted = _CountingReader(
                raw,
                self.cancel_check,
                self.cancel_interval,
                self.progress_callback,
                self.progress_interval,
            )
            with gzip.GzipFile(fileobj=counted) as stream:
                name = archive_name[:-3]
                bounded = self._bounded(stream, name, max(1, compressed_size), total)
                yield ArchiveMember(
                    name,
                    bounded,
                    compressed_size,
                    self.max_member,
                    (archive_name, name),
                )
                self._drain(bounded)
            if counted.count == 0 or bounded.count / counted.count > self.aggregate_ratio:
                raise ArchiveViolation("aggregate_ratio", name)
        if self.progress_callback is not None:
            self.progress_callback(compressed_size)

    def _targz(self, path: Path, archive_name: str) -> Iterator[ArchiveMember]:
        total = [0]
        compressed = path.stat().st_size
        with path.open("rb") as raw:
            counted_compressed = _CountingReader(
                raw,
                self.cancel_check,
                self.cancel_interval,
                self.progress_callback,
                self.progress_interval,
            )
            with gzip.GzipFile(fileobj=counted_compressed) as uncompressed:
                counted_tar = _CountingReader(
                    uncompressed,
                    self.cancel_check,
                    self.cancel_interval,
                    max_count=self.max_expanded,
                )
                yield from self._tar_members(counted_tar, archive_name, compressed, total)
        if compressed == 0 and total[0]:
            raise ArchiveViolation("aggregate_ratio")
        if compressed and counted_tar.count / compressed > self.aggregate_ratio:
            raise ArchiveViolation("aggregate_ratio")
        if self.progress_callback is not None:
            self.progress_callback(compressed)

    def _tar(self, path: Path, archive_name: str) -> Iterator[ArchiveMember]:
        total = [0]
        archive_size = path.stat().st_size
        with path.open("rb") as raw:
            counted_tar = _CountingReader(
                raw,
                self.cancel_check,
                self.cancel_interval,
                self.progress_callback,
                self.progress_interval,
                max_count=self.max_expanded,
            )
            yield from self._tar_members(counted_tar, archive_name, archive_size, total)
        if self.progress_callback is not None:
            self.progress_callback(archive_size)

    def _tar_members(
        self,
        stream: BinaryIO,
        archive_name: str,
        archive_size: int,
        total: list[int],
    ) -> Iterator[ArchiveMember]:
        seen: set[str] = set()
        count = 0
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for info in archive:
                if self.cancel_check is not None:
                    self.cancel_check()
                if info.isdir():
                    continue
                count += 1
                if count > self.max_members:
                    raise ArchiveViolation("member_count")
                try:
                    name = self.normalize_name(info.name, seen)
                    unsafe_pax_headers = set(info.pax_headers) - _SAFE_PAX_HEADERS
                    if not info.isreg() or unsafe_pax_headers or getattr(info, "sparse", None):
                        raise ArchiveViolation("unsafe_tar_type", name)
                except ArchiveViolation as warning:
                    self.warnings.append(warning)
                    continue
                if not is_supported_member(name):
                    self.warnings.append(ArchiveViolation("unsupported_member", name))
                    continue
                member_stream = archive.extractfile(info)
                if member_stream is None:
                    raise ArchiveViolation("corrupt_archive", name)
                bounded = self._bounded(member_stream, name, None, total)
                yield ArchiveMember(
                    name,
                    bounded,
                    archive_size,
                    info.size,
                    (archive_name, name),
                )
                self._drain(bounded)
