from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rby1_analyzer.cli.make_oracle import build_error_oracle
from rby1_analyzer.ingest.manifest import build_manifest, compare_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a corpus without modifying its oracle")
    parser.add_argument("root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--errors", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-crash", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    crashes: list[dict[str, str]] = []
    drift: list[str] = []
    missing_errors: list[dict] = []
    extra_errors: list[dict] = []
    error_count = 0
    actual = []
    try:
        actual = build_manifest(args.root)
        if args.manifest:
            expected = json.loads(args.manifest.read_text(encoding="utf-8"))
            drift = compare_manifest(expected, actual)
        if args.errors:
            expected_errors = json.loads(args.errors.read_text(encoding="utf-8"))
            observed_errors, archive_warnings = build_error_oracle(args.root)
            if archive_warnings:
                crashes.extend(
                    {"type": "ArchiveWarning", "message": json.dumps(item, sort_keys=True)}
                    for item in archive_warnings
                )
            expected_keys = {json.dumps(item, sort_keys=True): item for item in expected_errors}
            observed_keys = {json.dumps(item, sort_keys=True): item for item in observed_errors}
            missing_errors = [expected_keys[key] for key in sorted(expected_keys.keys() - observed_keys)]
            extra_errors = [observed_keys[key] for key in sorted(observed_keys.keys() - expected_keys)]
            error_count = len(observed_errors)
    except Exception as exc:                                                           
        crashes.append({"type": type(exc).__name__, "message": str(exc)})
    report = {
        "files": len(actual),
        "manifest_drift": drift,
        "uncaught_exceptions": crashes,
        "error_count": error_count,
        "error_oracle_missing": missing_errors,
        "error_oracle_extra": extra_errors,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "valid": not drift and not crashes and not missing_errors and not extra_errors,
    }
    print(json.dumps(report, sort_keys=True) if args.json else report)
    return 1 if drift or missing_errors or extra_errors or (args.fail_on_crash and crashes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
