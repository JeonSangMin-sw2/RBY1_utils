from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from pathlib import Path


class ArtifactKind(StrEnum):
    LOG = "log"
    CSV = "csv"
    ZIP = "zip"
    GZIP = "gzip"
    TAR = "tar"
    TAR_GZIP = "tar.gz"
    UNKNOWN = "unknown"


def classify(name: str, prefix: bytes = b"") -> ArtifactKind:
                                                                                 
    lower = Path(name).name.lower()
    if prefix.startswith(b"PK\x03\x04"):
        return ArtifactKind.ZIP
    if prefix.startswith(b"\x1f\x8b"):
        return ArtifactKind.TAR_GZIP if lower.endswith((".tar.gz", ".tgz")) else ArtifactKind.GZIP
    if lower.endswith(".zip"):
        return ArtifactKind.ZIP
    if lower.endswith((".tar.gz", ".tgz")):
        return ArtifactKind.TAR_GZIP
    if lower.endswith(".tar"):
        return ArtifactKind.TAR
    if lower.endswith(".gz"):
        return ArtifactKind.GZIP
    if lower.endswith(".log"):
        return ArtifactKind.LOG
    if lower.endswith(".csv"):
        return ArtifactKind.CSV
    return ArtifactKind.UNKNOWN


def is_supported_leaf(name: str) -> bool:
    return classify(name) in {ArtifactKind.LOG, ArtifactKind.CSV}


def is_supported_member(name: str) -> bool:
    return classify(name) != ArtifactKind.UNKNOWN
