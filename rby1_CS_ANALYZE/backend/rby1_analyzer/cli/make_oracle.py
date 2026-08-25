from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
from typing import BinaryIO

from rby1_analyzer.ingest.manifest import build_manifest, write_manifest
from rby1_analyzer.ingest.safe_archive_reader import ArchiveViolation, SafeArchiveReader
from rby1_analyzer.parsers import parse_rpc_log


_ERROR_LEVEL = re.compile(rb"\[error\]", re.IGNORECASE)


def _is_error_line(raw: bytes) -> bool:
    return _ERROR_LEVEL.search(raw) is not None


def error_entries(data: bytes | BinaryIO, path: str, member: str | None = None) -> list[dict]:
    stream = io.BytesIO(data) if isinstance(data, bytes) else data
    return [
        {
            "path": path,
            "member": member,
            "line": event.line,
            "offset": event.byte_offset,
            "severity": event.severity,
            "component": event.component,
            "raw_hash": event.raw_digest,
        }
        for event in parse_rpc_log(stream, member or path, event_filter=_is_error_line)
        if event.severity == "error"
    ]


def build_error_oracle(root: Path) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    warnings: list[dict] = []
    reader = SafeArchiveReader()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.lower().endswith(".log"):
            entries.extend(error_entries(path.read_bytes(), relative))
        elif relative.lower().endswith((".zip", ".gz", ".tar.gz", ".tgz")):
            try:
                for item in reader.read(path):
                    if item.name.lower().endswith(".log"):
                        entries.extend(error_entries(item.stream, relative, item.name))
            except (ArchiveViolation, OSError) as exc:
                warnings.append({"path": relative, "error": str(exc)})
    entries.sort(key=lambda entry: (entry["path"], entry["member"] or "", entry["offset"]))
    return entries, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly regenerate reviewed corpus oracles")
    parser.add_argument("root", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--errors", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.root)
    errors, warnings = build_error_oracle(args.root)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, args.manifest)
    args.errors.write_text(json.dumps(errors, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"manifest_count": len(manifest), "error_count": len(errors), "warnings": warnings},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
