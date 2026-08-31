from __future__ import annotations

import datetime as dt
import hashlib
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .database import Database
from .time_alignment import align_naive_log_wall_times


@dataclass(frozen=True, slots=True)
class CasePaths:
    root: Path

    @property
    def database(self) -> Path:
        return self.root / "case.sqlite"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    def content(self, kind: str, digest: str) -> Path:
        return self.root / kind / "sha256" / digest[:2] / digest


class CaseStore:
    def __init__(self, data_root: Path):
        self.root = (data_root / "cases").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._verified: dict[Path, tuple[int, int, str]] = {}
        self._verified_cases: set[str] = set()
        self._verification_lock = threading.Lock()
        self._integrate_legacy_cases()

    def _integrate_legacy_cases(self) -> None:
        user_home = Path.home()
        legacy_candidates = [
            user_home / ".local" / "share" / "rby1-cs-analyzer-v3" / "cases",
            user_home / ".local" / "share" / "rby1-cs-analyzer-v2" / "cases",
            user_home / "AppData" / "Local" / "RB-Y1 CS Analyzer V3" / "cases",
            user_home / "AppData" / "Local" / "RB-Y1 CS Analyzer V2" / "cases",
        ]
        import shutil
        for lroot in legacy_candidates:
            if not lroot.is_dir() or lroot == self.root:
                continue
            for cdir in lroot.iterdir():
                if cdir.is_dir() and (cdir / "case.sqlite").is_file():
                    target = self.root / cdir.name
                    if not target.exists():
                        try:
                            shutil.copytree(cdir, target)
                        except Exception:
                            pass

    def paths(self, case_id: str) -> CasePaths:
        if not case_id or any(c not in "0123456789abcdef-" for c in case_id):
            raise ValueError("invalid case id")
        return CasePaths(self.root / case_id)

    def create(self) -> str:
        case_id = str(uuid.uuid4())
        paths = self.paths(case_id)
        paths.temp.mkdir(parents=True)
        (paths.root / "logs").mkdir()
        db = Database(paths.database)
        with db.connect() as connection:
            connection.execute(
                "INSERT INTO cases(id, created_at) VALUES (?, ?)",
                (case_id, dt.datetime.now(dt.timezone.utc).isoformat()),
            )
        return case_id

    def delete(self, case_id: str) -> bool:
        paths = self.paths(case_id)
        import shutil
        deleted = False
        if paths.root.exists():
            shutil.rmtree(paths.root, ignore_errors=True)
            deleted = True

        user_home = Path.home()
        legacy_candidates = [
            user_home / ".local" / "share" / "rby1-cs-analyzer-v3" / "cases" / case_id,
            user_home / ".local" / "share" / "rby1-cs-analyzer-v2" / "cases" / case_id,
            user_home / "AppData" / "Local" / "RB-Y1 CS Analyzer V3" / "cases" / case_id,
            user_home / "AppData" / "Local" / "RB-Y1 CS Analyzer V2" / "cases" / case_id,
        ]
        for lcase in legacy_candidates:
            if lcase.exists():
                shutil.rmtree(lcase, ignore_errors=True)
                deleted = True

        with self._verification_lock:
            self._verified_cases.discard(case_id)
        return deleted

    def update_title(self, case_id: str, title: str) -> str:
        clean_title = title.strip()
        db = self.open(case_id)
        with db.connect() as connection:
            connection.execute("UPDATE cases SET title=? WHERE id=?", (clean_title if clean_title else None, case_id))
        return clean_title

    def open(self, case_id: str) -> Database:
        paths = self.paths(case_id)
        if not paths.database.is_file():
            raise FileNotFoundError(case_id)
        database = Database(paths.database)
        with self._verification_lock:
            already_verified = case_id in self._verified_cases
        if not already_verified:
            self._verify_artifacts(database)
            align_naive_log_wall_times(database)
            with self._verification_lock:
                self._verified_cases.add(case_id)
        return database

    def _verify_artifacts(self, database: Database) -> None:
        with database.connect() as connection:
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chart_samples_art_time ON chart_samples(artifact_id, sample_time)"
            )
            rows = connection.execute(
                "SELECT id,sha256,size,stored_path,status FROM artifacts"
            ).fetchall()
        for row in rows:
            if row["status"] == "degraded_missing_artifact":
                continue
            path = Path(row["stored_path"])
            if not path.is_absolute():
                path = path.resolve()
            detail: str | None = None
            try:
                stat = path.stat()
                if not path.is_file() or stat.st_size != row["size"]:
                    detail = "artifact_size_mismatch"
                else:
                    fingerprint = (stat.st_size, stat.st_mtime_ns, row["sha256"])
                    with self._verification_lock:
                        verified = self._verified.get(path) == fingerprint
                    if not verified:
                        digest = hashlib.sha256()
                        with path.open("rb") as source:
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                        if digest.hexdigest() != row["sha256"]:
                            detail = "artifact_hash_mismatch"
                        else:
                            with self._verification_lock:
                                self._verified[path] = fingerprint
            except OSError:
                detail = "missing_artifact"
            if detail is not None:
                self._mark_degraded(database, int(row["id"]), detail, path)

    @staticmethod
    def _mark_degraded(
        database: Database,
        artifact_id: int,
        detail: str,
        path: Path,
    ) -> None:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE artifacts SET status='degraded_missing_artifact',detail_code=? "
                "WHERE id=?",
                (detail, artifact_id),
            )
            existing = connection.execute(
                "SELECT 1 FROM warnings WHERE artifact_id=? "
                "AND code='degraded_missing_artifact' LIMIT 1",
                (artifact_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO warnings(artifact_id,code,message) VALUES (?,?,?)",
                    (
                        artifact_id,
                        "degraded_missing_artifact",
                        f"Retained evidence is unavailable or invalid: {path.name} ({detail})",
                    ),
                )
            connection.execute("COMMIT")
