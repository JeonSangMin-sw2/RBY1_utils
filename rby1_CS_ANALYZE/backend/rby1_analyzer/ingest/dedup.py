from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Provenance:
    source: str
    member: str | None = None
    archive_chain: tuple[str, ...] = ()


@dataclass
class DeduplicatedArtifact:
    sha256: str
    size: int
    provenances: list[Provenance] = field(default_factory=list)


class Deduplicator:
                                                                 

    def __init__(self) -> None:
        self._items: dict[str, DeduplicatedArtifact] = {}

    def add_chunks(self, chunks: Iterable[bytes], provenance: Provenance) -> DeduplicatedArtifact:
        digest, size = hashlib.sha256(), 0
        for chunk in chunks:
            digest.update(chunk)
            size += len(chunk)
        key = digest.hexdigest()
        item = self._items.setdefault(key, DeduplicatedArtifact(key, size))
        if item.size != size:
            raise RuntimeError("sha256 size invariant violated")
        item.provenances.append(provenance)
        return item

    def add_file(
        self, path: Path, provenance: Provenance, chunk_size: int = 1024 * 1024
    ) -> DeduplicatedArtifact:
        with path.open("rb") as stream:
            return self.add_chunks(iter(lambda: stream.read(chunk_size), b""), provenance)

    @property
    def artifacts(self) -> tuple[DeduplicatedArtifact, ...]:
        return tuple(self._items.values())
