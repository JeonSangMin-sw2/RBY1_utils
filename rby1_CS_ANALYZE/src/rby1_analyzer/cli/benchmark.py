                                                                

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

from rby1_analyzer.analysis import AnalysisCounts, analyze_upload
from rby1_analyzer.charts.repository import DEFAULT_SERIES, SQLiteChartRepository
from rby1_analyzer.charts.service import window_series
from rby1_analyzer.ingest.upload import ingest_upload
from rby1_analyzer.storage.cases import CaseStore


def _files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    yield from sorted(path for path in root.rglob("*") if path.is_file())


def _representative_import(source: Path) -> dict[str, object]:
    counts = AnalysisCounts()
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    with tempfile.TemporaryDirectory(prefix="rby1-benchmark-") as directory:
        cases = CaseStore(Path(directory))
        case_id = cases.create()
        db = cases.open(case_id)
        paths = cases.paths(case_id)
        for path in _files(source):
            file_count += 1
            with path.open("rb") as input_stream:
                while chunk := input_stream.read(1024 * 1024):
                    byte_count += len(chunk)
                    digest.update(chunk)
            with path.open("rb") as input_stream:
                stored = ingest_upload(
                    db,
                    paths,
                    case_id,
                    path.name,
                    input_stream,
                    content_length=path.stat().st_size,
                )
            counts.add(analyze_upload(db, paths, case_id, path.name, stored))
    return {
        "file_count": file_count,
        "byte_count": byte_count,
        "content_sha256": digest.hexdigest(),
        "analysis_counts": {
            "sources": counts.sources,
            "members": counts.members,
            "events": counts.events,
            "samples": counts.samples,
            "warnings": counts.warnings,
        },
    }


def _cached_chart_query(selected: list[str]) -> dict[str, object]:
    names = selected or list(DEFAULT_SERIES)
    with tempfile.TemporaryDirectory(prefix="rby1-chart-benchmark-") as directory:
        cases = CaseStore(Path(directory))
        case_id = cases.create()
        db = cases.open(case_id)
        with db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO artifacts(case_id,kind,sha256,size,stored_path,original_name,status) "
                "VALUES (?, 'source', ?, 0, '', 'benchmark.csv', 'parsed')",
                (case_id, "0" * 64),
            )
            artifact_id = int(cursor.lastrowid)
            rows = []
            for index, name in enumerate(names):
                kind = "discrete" if name.startswith("power_") or "state" in name else "continuous"
                for sample in range(5_001):
                    sample_time = sample / 500
                    value = (
                        float((sample // 250) % 2)
                        if kind == "discrete"
                        else sample * (index + 1) / 500
                    )
                    rows.append((artifact_id, sample_time, name, value, kind))
            connection.executemany(
                "INSERT INTO chart_samples(artifact_id,sample_time,name,value,kind) "
                "VALUES (?,?,?,?,?)",
                rows,
            )
        started = time.perf_counter()
        loaded = SQLiteChartRepository(cases).load_series(case_id, set(names), 0.0, 10.0)
        result = window_series(loaded, start=0.0, end=10.0, selected=set(names))
        elapsed = time.perf_counter() - started
    return {
        "elapsed_seconds": elapsed,
        "file_count": 0,
        "byte_count": 0,
        "content_sha256": None,
        "series_count": len(result),
        "point_count": sum(len(item.points) for item in result),
    }


def _windows_peak_rss_mb() -> float:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_fault_count", wintypes.DWORD),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.peak_working_set_size / 1024**2


def _peak_rss_mb(platform_name: str | None = None) -> float:
    if (platform_name or os.name) == "nt":
        return _windows_peak_rss_mb()

    import resource

                                                                   
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _node_version() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    return subprocess.run(
        [node, "--version"], check=False, capture_output=True, text=True, timeout=5
    ).stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--case", default="representative_import")
    parser.add_argument("--max-ms", type=float)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--max-rss-mb", type=float)
    parser.add_argument("--cache-state", choices=("cold", "warm", "unknown"), default="unknown")
    parser.add_argument("--series", action="append", default=[])
    parser.add_argument("--concurrency", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.source.exists():
        print(json.dumps({"ok": False, "error": "source_not_found", "source": str(args.source)}))
        return 2
    if args.concurrency < 1:
        print(json.dumps({"ok": False, "error": "invalid_concurrency"}))
        return 2

    if args.case == "chart_cached_10s":
        workload = "cached_sqlite_chart_window"
        metrics = _cached_chart_query(args.series)
        elapsed = float(metrics.pop("elapsed_seconds"))
    else:
        workload = "retained_source_import_and_analysis"
        started = time.perf_counter()
        metrics = _representative_import(args.source)
        elapsed = time.perf_counter() - started
    rss = _peak_rss_mb()
    free_disk = shutil.disk_usage(args.source if args.source.is_dir() else args.source.parent).free
    failures = []
    if args.max_ms is not None and elapsed * 1000 > args.max_ms:
        failures.append("max_ms")
    if args.max_seconds is not None and elapsed > args.max_seconds:
        failures.append("max_seconds")
    if args.max_rss_mb is not None and rss > args.max_rss_mb:
        failures.append("max_rss_mb")

    report = {
        "ok": not failures,
        "case": args.case,
        "workload": workload,
        "source": str(args.source),
        "cache_state": args.cache_state,
        "selected_series": args.series,
        "concurrency": args.concurrency,
        "elapsed_seconds": elapsed,
        "peak_rss_mb": rss,
        **metrics,
        "free_disk_bytes": free_disk,
        "cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "node": _node_version(),
        "frontend_build_present": (Path(__file__).parents[3] / "frontend" / "dist").is_dir(),
        "threshold_failures": failures,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
