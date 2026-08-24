from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .classifier import classify


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str
    kind: str
    mtime: float


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> list[ManifestEntry]:
    root = root.resolve()
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = PurePosixPath(path.relative_to(root)).as_posix()
        stat = path.stat()
        with path.open("rb") as stream:
            prefix = stream.read(4)
        entries.append(
            ManifestEntry(
                relative,
                stat.st_size,
                _hash(path),
                classify(relative, prefix).value,
                stat.st_mtime,
            )
        )
    return entries


def write_manifest(entries: list[ManifestEntry], output: Path) -> None:
    output.write_text(
        json.dumps([asdict(entry) for entry in entries], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def compare_manifest(expected: list[dict], actual: list[ManifestEntry]) -> list[str]:
                                                                                   
    fields = ("path", "size", "sha256", "kind")
    left = {entry["path"]: tuple(entry.get(field) for field in fields[1:]) for entry in expected}
    right = {entry.path: tuple(getattr(entry, field) for field in fields[1:]) for entry in actual}
    return [
        f"{path}: {left.get(path)!r} -> {right.get(path)!r}"
        for path in sorted(left.keys() | right.keys())
        if left.get(path) != right.get(path)
    ]
