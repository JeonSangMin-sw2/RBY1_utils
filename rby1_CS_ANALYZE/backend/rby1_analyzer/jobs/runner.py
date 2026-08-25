from __future__ import annotations

import ctypes
import multiprocessing as mp
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rby1_analyzer.analysis import AnalysisCounts, analyze_upload
from rby1_analyzer.core.states import JobState
from rby1_analyzer.storage.cases import CaseStore
from rby1_analyzer.storage.content import StoredContent
from rby1_analyzer.incidents.builder import rebuild_incidents

from .manager import JobManager


_HAS_PROCESS_GROUPS = hasattr(os, "killpg")


@dataclass(frozen=True, slots=True)
class ImportRef:
    filename: str
    digest: str
    size: int
    path: str
    duplicate: bool

    @classmethod
    def from_stored(cls, filename: str, stored: StoredContent) -> "ImportRef":
        return cls(filename, stored.digest, stored.size, str(stored.path), stored.duplicate)

    def as_stored(self) -> StoredContent:
        return StoredContent(self.digest, self.size, Path(self.path), self.duplicate)


def _run_import(data_root: str, case_id: str, job_id: str, refs: list[ImportRef]) -> None:
    cases = CaseStore(Path(data_root))
    db = cases.open(case_id)
    jobs = JobManager()
    counts = AnalysisCounts()
    total = sum(item.size for item in refs)
    current_processed = 0
    last_processed = -1
    last_percent = -1
    last_emit_at = 0.0
    phase = "preparing"
    current_item: str | None = None

    def counter_snapshot(processed: int, *, complete: bool = False) -> dict[str, int]:
        normalized = max(0, min(total, processed))
        if complete:
            percent = 100
        elif total:
            normalized = min(normalized, total - 1)
            percent = min(99, normalized * 100 // total)
        else:
            percent = 0
        return {
            "received_bytes": total,
            "total_bytes": total,
            "processed_bytes": normalized,
            "progress_percent": percent,
            "sources": counts.sources,
            "members": counts.members,
            "events": counts.events,
            "samples": counts.samples,
            "warnings": counts.warnings,
            "incidents": counts.incidents,
        }

    def emit_progress(processed: int, *, force: bool = False) -> None:
        nonlocal current_processed, last_emit_at, last_percent, last_processed
        snapshot = counter_snapshot(processed)
        current_processed = snapshot["processed_bytes"]
        percent = snapshot["progress_percent"]
        now = time.monotonic()
        if not force and percent == last_percent and (
            current_processed == last_processed or now - last_emit_at < 0.5
        ):
            return
        jobs.append(
            db,
            job_id,
            {
                "state": JobState.RUNNING,
                "phase": phase,
                "current_item": current_item,
                "counters": snapshot,
                "warning_delta": [],
            },
        )
        last_processed = current_processed
        last_percent = percent
        last_emit_at = now

    def report_status(next_phase: str, item: str | None) -> None:
        nonlocal current_item, phase
        phase = next_phase
        current_item = item
        emit_progress(current_processed, force=True)

    def cancel_check() -> None:
        with db.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is not None and row["cancel_requested"]:
            raise InterruptedError("cancel_requested")

    try:
        with db.connect() as connection:
            connection.execute(
                "UPDATE jobs SET state=?,updated_at=datetime('now') WHERE id=?",
                (JobState.RUNNING, job_id),
            )
        emit_progress(0, force=True)
        completed_bytes = 0
        for ref in refs:
            cancel_check()

            def source_progress(processed: int, *, base: int = completed_bytes) -> None:
                source_max = max(0, ref.size - 1)
                emit_progress(base + min(max(0, processed), source_max))

            result = analyze_upload(
                db,
                cases.paths(case_id),
                case_id,
                ref.filename,
                StoredContent(ref.digest, ref.size, Path(ref.path), ref.duplicate),
                cancel_check=cancel_check,
                progress_callback=source_progress,
                status_callback=report_status,
            )
            counts.add(result)
            completed_bytes += ref.size
            emit_progress(completed_bytes, force=True)
        report_status("building_incidents", None)
        counts.incidents = rebuild_incidents(db, case_id, job_id=job_id)

        # Write consolidated chronological timeline stream
        try:
            from rby1_analyzer.api.routes.csv_analysis import _ensure_timeline_files, generate_csv_meta
            runtime_stub = type("RuntimeStub", (), {"cases": cases})()
            _ensure_timeline_files(runtime_stub, case_id)
            generate_csv_meta(cases, case_id)
        except Exception:
            pass

        emit_progress(total, force=True)
        with db.connect() as connection:
            connection.execute(
                "UPDATE jobs SET state=?,updated_at=datetime('now') WHERE id=?",
                (JobState.COMPLETE, job_id),
            )
        jobs.append(
            db,
            job_id,
            {
                "state": JobState.COMPLETE,
                "phase": "complete",
                "current_item": None,
                "counters": counter_snapshot(total, complete=True),
                "warning_delta": [],
            },
        )
    except InterruptedError:
        _finish_cancelled(cases, case_id, job_id, counter_snapshot(current_processed))
    except BaseException as exc:
        detail = getattr(exc, "detail_code", type(exc).__name__)
        with db.connect() as connection:
            connection.execute(
                "UPDATE jobs SET state=?,detail_code=?,updated_at=datetime('now') WHERE id=?",
                (JobState.FAILED, str(detail), job_id),
            )
        jobs.append(
            db,
            job_id,
            {
                "state": JobState.FAILED,
                "detail_code": str(detail),
                "counters": counter_snapshot(current_processed),
                "warning_delta": [],
            },
        )


def _finish_cancelled(
    cases: CaseStore,
    case_id: str,
    job_id: str,
    counters: dict[str, int] | None = None,
) -> None:
    db = cases.open(case_id)
    with db.connect() as connection:
        row = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["state"] in {
        JobState.CANCELLED,
        JobState.COMPLETE,
        JobState.FAILED,
    }:
        return
    temp = cases.paths(case_id).temp
    for path in temp.glob("*.part"):
        path.unlink(missing_ok=True)
    remaining = [str(path) for path in temp.iterdir() if path.is_file()]
    state = JobState.CANCELLED if not remaining else JobState.FAILED
    detail = None if not remaining else "cleanup_incomplete"
    with db.connect() as connection:
        connection.execute(
            "UPDATE artifacts SET status='partial',detail_code='cancelled_partial' "
            "WHERE status IN ('stored','parsing')"
        )
        connection.execute(
            "UPDATE jobs SET state=?,detail_code=?,updated_at=datetime('now') WHERE id=?",
            (state, detail, job_id),
        )
    JobManager().append(
        db,
        job_id,
        {
            "state": state,
            "detail_code": detail,
            "counters": counters or {},
            "warning_delta": [],
            "cleanup": {"remaining_temp": remaining},
        },
    )


def _finish_orphaned_worker(
    cases: CaseStore,
    case_id: str,
    job_id: str,
    exit_code: int | None,
) -> None:
                                                                              
    db = cases.open(case_id)
    manager = JobManager()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT state,cancel_requested FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if row is None or row["state"] in {
            JobState.CANCELLED,
            JobState.COMPLETE,
            JobState.FAILED,
        }:
            connection.execute("COMMIT")
            return
        cancelled = bool(row["cancel_requested"]) or row["state"] == JobState.CANCEL_REQUESTED
        if cancelled:
            connection.execute("COMMIT")
            _finish_cancelled(cases, case_id, job_id)
            return
        detail = "analysis_worker_crashed" if exit_code not in {None, 0} else "analysis_worker_exited"
        connection.execute(
            "UPDATE jobs SET state=?,detail_code=?,updated_at=datetime('now') WHERE id=?",
            (JobState.FAILED, detail, job_id),
        )
        connection.execute("COMMIT")
    manager.append(
        db,
        job_id,
        {
            "state": JobState.FAILED,
            "detail_code": detail,
            "counters": {},
            "warning_delta": [],
        },
    )


def _run_import_process(
    data_root: str,
    case_id: str,
    job_id: str,
    refs: list[ImportRef],
    parent_pid: int,
) -> None:
    if hasattr(os, "setsid"):
        os.setsid()
    _terminate_with_parent(parent_pid)
    _run_import(data_root, case_id, job_id, refs)


def _terminate_with_parent(parent_pid: int) -> None:
                                                                                    
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    if os.getppid() != parent_pid:
        raise SystemExit("analysis parent exited during startup")


def _stop_process(process: mp.Process, *, force: bool) -> None:
    if _HAS_PROCESS_GROUPS and process.pid is not None:
        signal_number = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(process.pid, signal_number)
            return
        except (ProcessLookupError, PermissionError):
            pass
    if force:
        process.kill()
    else:
        process.terminate()


@dataclass(frozen=True, slots=True)
class _ActiveProcess:
    case_id: str
    process: mp.Process


class ProcessJobRunner:
    def __init__(self, cases: CaseStore):
        self.cases = cases
        self._context = mp.get_context("spawn")
        self._processes: dict[str, _ActiveProcess] = {}
        self._lock = threading.Lock()

    def start(self, case_id: str, job_id: str, refs: list[ImportRef]) -> None:
        process = self._context.Process(
            target=_run_import_process,
            args=(str(self.cases.root.parent), case_id, job_id, refs, os.getpid()),
            daemon=False,
            name=f"rby1-analysis-{job_id[:8]}",
        )
        process.start()
        with self._lock:
            self._processes[job_id] = _ActiveProcess(case_id, process)
        threading.Thread(
            target=self._reap,
            args=(job_id, process),
            daemon=True,
            name=f"rby1-reaper-{job_id[:8]}",
        ).start()

    def _reap(self, job_id: str, process: mp.Process) -> None:
        process.join()
        with self._lock:
            active = self._processes.get(job_id)
            if active is None or active.process is not process:
                return
            self._processes.pop(job_id, None)
        try:
            _finish_orphaned_worker(
                self.cases,
                active.case_id,
                job_id,
                process.exitcode,
            )
        except (FileNotFoundError, KeyError, ValueError):
            return

    def cancel(self, case_id: str, job_id: str) -> bool:
        with self._lock:
            active = self._processes.get(job_id)
        process = active.process if active is not None else None
        if process is not None and process.is_alive():
            process.join(timeout=5)
            if process.is_alive():
                _stop_process(process, force=False)
                process.join(timeout=5)
            if process.is_alive():
                _stop_process(process, force=True)
                process.join()
        with self._lock:
            current = self._processes.get(job_id)
            if current is not None and current.process is process:
                self._processes.pop(job_id, None)
        _finish_cancelled(self.cases, case_id, job_id)
        return True

    def shutdown(self) -> None:
                                                                        
        with self._lock:
            active = list(self._processes.items())
        for job_id, item in active:
            try:
                db = self.cases.open(item.case_id)
                JobManager().request_cancel(db, job_id)
            except (FileNotFoundError, KeyError, ValueError):
                pass
        for job_id, item in active:
            self.cancel(item.case_id, job_id)
