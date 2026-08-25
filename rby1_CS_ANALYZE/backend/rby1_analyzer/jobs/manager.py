from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from rby1_analyzer.core.states import JobState
from rby1_analyzer.storage.database import Database

TERMINAL = {JobState.CANCELLED, JobState.FAILED, JobState.COMPLETE}
ACTIVE = {JobState.QUEUED, JobState.RUNNING, JobState.CANCEL_REQUESTED}


class ActiveImportError(RuntimeError):
    detail_code = "import_already_running"

    def __init__(self, case_id: str):
        super().__init__(self.detail_code)
        self.case_id = case_id


@dataclass(frozen=True, slots=True)
class ProgressRecord:
    seq: int
    payload: dict[str, Any]


class JobManager:
    def __init__(self):
        self._conditions: dict[str, threading.Condition] = {}
        self._lock = threading.Lock()

    def _condition(self, job_id: str) -> threading.Condition:
        with self._lock:
            return self._conditions.setdefault(job_id, threading.Condition())

    def create(self, db: Database, case_id: str) -> str:
        job_id = str(uuid.uuid4())
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id FROM jobs WHERE case_id=? AND state IN (?,?,?) LIMIT 1",
                (case_id, *sorted(str(state) for state in ACTIVE)),
            ).fetchone()
            if active is not None:
                connection.execute("ROLLBACK")
                raise ActiveImportError(case_id)
            connection.execute(
                "INSERT INTO jobs(id,case_id,state,created_at,updated_at) VALUES (?,?,?,?,?)",
                (job_id, case_id, JobState.QUEUED, now, now),
            )
            connection.execute("COMMIT")
        self.append(db, job_id, {"state": JobState.QUEUED, "counters": {}, "warning_delta": []})
        return job_id

    def append(self, db: Database, job_id: str, fields: dict[str, Any]) -> ProgressRecord:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM progress WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            seq = int(row["seq"])
            payload = {"seq": seq, "job_id": job_id, "server_time": now, **fields}
            connection.execute(
                "INSERT INTO progress(job_id,seq,payload) VALUES (?,?,?)",
                (job_id, seq, json.dumps(payload, separators=(",", ":"))),
            )
            connection.execute("COMMIT")
        condition = self._condition(job_id)
        with condition:
            condition.notify_all()
        return ProgressRecord(seq, payload)

    def request_cancel(self, db: Database, job_id: str) -> bool:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KeyError(job_id)
            state = JobState(row["state"])
            if state in TERMINAL or state == JobState.CANCEL_REQUESTED:
                connection.execute("COMMIT")
                return False
            connection.execute(
                "UPDATE jobs SET state=?,cancel_requested=1,updated_at=? WHERE id=?",
                (JobState.CANCEL_REQUESTED, now, job_id),
            )
            connection.execute("COMMIT")
        self.append(db, job_id, {"state": JobState.CANCEL_REQUESTED, "counters": {}, "warning_delta": []})
        return True

    def get(self, db: Database, job_id: str) -> dict[str, Any]:
        with db.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            progress = connection.execute(
                "SELECT payload FROM progress WHERE job_id=? ORDER BY seq DESC LIMIT 1", (job_id,)
            ).fetchone()
        result = dict(row)
        result["snapshot"] = json.loads(progress["payload"]) if progress else None
        return result

    def records(self, db: Database, job_id: str, after_seq: int) -> list[ProgressRecord]:
        with db.connect() as connection:
            rows = connection.execute(
                "SELECT seq,payload FROM progress WHERE job_id=? AND seq>? ORDER BY seq",
                (job_id, after_seq),
            ).fetchall()
        return [ProgressRecord(row["seq"], json.loads(row["payload"])) for row in rows]

    def stream(self, db: Database, job_id: str, after_seq: int, heartbeat: float = 5.0) -> Iterator[dict[str, Any]]:
        cursor = after_seq
        condition = self._condition(job_id)
        last_heartbeat = time.monotonic()
        while True:
            records = self.records(db, job_id, cursor)
            for record in records:
                cursor = record.seq
                yield record.payload
            snapshot = self.get(db, job_id)
            if JobState(snapshot["state"]) in TERMINAL and not records:
                return
            if records:
                continue
            with condition:
                condition.wait(timeout=min(heartbeat, 0.25))
            if self.records(db, job_id, cursor):
                continue
            if time.monotonic() - last_heartbeat >= heartbeat:
                last_heartbeat = time.monotonic()
                yield {
                    "seq": cursor,
                    "job_id": job_id,
                    "state": snapshot["state"],
                    "heartbeat": True,
                    "server_time": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
