from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class StorageExhausted(RuntimeError):
    detail_code = "storage_exhausted"


@dataclass(frozen=True, slots=True)
class StoredContent:
    digest: str
    size: int
    path: Path
    duplicate: bool


def retain_stream(stream: BinaryIO, target_for_digest, temp_dir: Path, *, limit: int) -> StoredContent:
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / f"upload-{os.getpid()}-{os.urandom(8).hex()}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp.open("xb") as output:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError("upload_too_large")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        hexdigest = digest.hexdigest()
        target = target_for_digest(hexdigest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            temp.unlink()
            return StoredContent(hexdigest, size, target, True)
        os.replace(temp, target)
        return StoredContent(hexdigest, size, target, False)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise StorageExhausted from exc
        raise
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
