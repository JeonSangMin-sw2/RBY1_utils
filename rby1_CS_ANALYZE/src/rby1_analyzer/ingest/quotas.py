from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class Quotas:
    upload_file: int = 2 * GIB
    upload_batch: int = 2 * GIB
    expanded_import: int = 3 * GIB
    case_storage: int = 8 * GIB
    global_storage: int = 20 * GIB
    temp_job: int = 2 * GIB
    members: int = 10_000
    depth: int = 3
    member_size: int = 3 * GIB // 2
    raw_line: int = 4 * 1024**2


@dataclass(frozen=True, slots=True)
class DiskProjection:
    capacity: int
    free: int
    projected_write: int
    reserve: int

    @property
    def accepted(self) -> bool:
        return self.free - self.projected_write >= self.reserve


def disk_projection(path: Path, projected_write: int) -> DiskProjection:
    usage = shutil.disk_usage(path)
    capacity = usage.total
    free = usage.free
    reserve = max(2 * GIB, min(4 * GIB, capacity // 10))
    return DiskProjection(capacity, free, projected_write, reserve)


def require_capacity(path: Path, projected_write: int) -> DiskProjection:
    projection = disk_projection(path, projected_write)
    if not projection.accepted:
        raise ValueError("insufficient_storage")
    return projection


def tree_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except FileNotFoundError:
            continue
    return total


def require_storage_limits(
    case_root: Path,
    cases_root: Path,
    temp_root: Path,
    projected_write: int,
    *,
    quotas: Quotas = Quotas(),
    temporary_write: int | None = None,
) -> None:
    if projected_write < 0:
        raise ValueError("invalid_projected_write")
    temp_projection = projected_write if temporary_write is None else temporary_write
    if temp_projection > quotas.temp_job:
        raise ValueError("temp_quota_exceeded")
    if tree_bytes(temp_root) + temp_projection > quotas.temp_job:
        raise ValueError("temp_quota_exceeded")
    if tree_bytes(case_root) + projected_write > quotas.case_storage:
        raise ValueError("case_quota_exceeded")
    if tree_bytes(cases_root) + projected_write > quotas.global_storage:
        raise ValueError("global_quota_exceeded")
    require_capacity(case_root, projected_write)
