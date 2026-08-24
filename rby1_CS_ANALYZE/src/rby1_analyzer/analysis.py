from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import BinaryIO, Callable, TextIO

from rby1_analyzer.diagnostics import DEFAULT_RULES, evaluate_event
from rby1_analyzer.ingest.classifier import ArtifactKind, classify
from rby1_analyzer.ingest.quotas import Quotas, require_storage_limits
from rby1_analyzer.ingest.safe_archive_reader import ArchiveViolation, SafeArchiveReader
from rby1_analyzer.parsers import parse_fault_csv, parse_rpc_log
from rby1_analyzer.storage.cases import CasePaths
from rby1_analyzer.storage.content import StoredContent, retain_stream
from rby1_analyzer.storage.database import Database
from rby1_analyzer.storage.time_alignment import align_naive_log_wall_times
from rby1_analyzer.timeline.time import TimeObservation, parse_fault_time


EventInsert = tuple[tuple[object, ...], tuple[TimeObservation, ...]]
StatusCallback = Callable[[str, str | None], None]

_CSV_INSERT_BATCH = 50_000

_LOG_EVENT_MARKERS = (
    b"[warning]",
    b"[warn]",
    b"[error]",
    b"[critical]",
    b"minorfault",
    b"majorfault",
    b"state changed",
    b"state transitioned",
    b"rby1 model:",
    b"model version:",
    b"rby1 version:",
    b"rby1-sdk version:",
)
_LOG_RESULT_MARKERS = (
    b"success",
    b"succeeded",
    b"completed",
    b"failed",
    b"failure",
    b"timeout",
    b"cancelled",
    b"canceled",
)
_LOG_QUOTED_VALUE = re.compile(rb'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_LOG_TIMEOUT_ARGUMENT = re.compile(
    rb"\btimeout\s*\{\s*(?:(?:seconds|nanos)\s*:\s*-?\d+\s*)*\}",
    re.IGNORECASE,
)
_LOG_COMMAND_CANDIDATE = re.compile(
    rb"\b(?:command|request|rpc)\b\s*[:=]?\s*[A-Za-z]",
    re.IGNORECASE,
)
_LOG_SERVICE_COMMAND_REQUEST = re.compile(
    rb"\[Service::[^\]]*Command[^\]]*\]\s*Requested\s*:",
    re.IGNORECASE,
)
_LOG_OPERATION_STEP = re.compile(
    rb"\b(?:engaging|disengaging) brakes\b|\bbrakes (?:engaged|disengaged)\b|"
    rb"\bswitched to gravity compensation mode\b",
    re.IGNORECASE,
)


def _potential_log_event(raw: bytes) -> bool:
    lowered = raw.lower()
    if any(marker in lowered for marker in _LOG_EVENT_MARKERS):
        return True
    if any(marker in lowered for marker in _LOG_RESULT_MARKERS):
        unquoted = _LOG_QUOTED_VALUE.sub(b"", raw).lower()
        unquoted = _LOG_TIMEOUT_ARGUMENT.sub(b"", unquoted)
        if any(marker in unquoted for marker in _LOG_RESULT_MARKERS):
            return True
    return bool(
        _LOG_COMMAND_CANDIDATE.search(raw)
        or _LOG_SERVICE_COMMAND_REQUEST.search(raw)
        or _LOG_OPERATION_STEP.search(raw)
    )


@dataclass(slots=True)
class AnalysisCounts:
    sources: int = 0
    members: int = 0
    events: int = 0
    samples: int = 0
    warnings: int = 0
    incidents: int = 0

    def add(self, other: "AnalysisCounts") -> None:
        self.sources += other.sources
        self.members += other.members
        self.events += other.events
        self.samples += other.samples
        self.warnings += other.warnings
        self.incidents += other.incidents


@dataclass(slots=True)
class _ArchiveBudget:
    members: int = 0
    expanded: int = 0


def _artifact_row(db: Database, case_id: str, kind: str, digest: str) -> sqlite3.Row:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE case_id=? AND kind=? AND sha256=?",
            (case_id, kind, digest),
        ).fetchone()
    if row is None:
        raise KeyError((case_id, kind, digest))
    return row


def _reset_artifact_analysis(db: Database, case_id: str, artifact_id: int) -> None:
                                                                              
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
                                                                                      
            connection.execute("DELETE FROM analysis_runs WHERE case_id=?", (case_id,))
            connection.execute(
                "DELETE FROM correlations WHERE artifact_id=? "
                "OR request_event_id IN (SELECT id FROM events WHERE artifact_id=?) "
                "OR result_event_id IN (SELECT id FROM events WHERE artifact_id=?)",
                (artifact_id, artifact_id, artifact_id),
            )
            connection.execute("DELETE FROM time_observations WHERE artifact_id=?", (artifact_id,))
            connection.execute("DELETE FROM chart_samples WHERE artifact_id=?", (artifact_id,))
            connection.execute("DELETE FROM events WHERE artifact_id=?", (artifact_id,))
            connection.execute("DELETE FROM warnings WHERE artifact_id=?", (artifact_id,))
            connection.execute(
                "UPDATE artifacts SET status='stored',detail_code=NULL WHERE id=?",
                (artifact_id,),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise


def _warning(
    db: Database,
    artifact_id: int | None,
    code: str,
    message: str,
    member_name: str | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO warnings(artifact_id,code,message,member_name) VALUES (?,?,?,?)",
            (artifact_id, code, message[:1000], member_name),
        )


def _interesting(event) -> bool:
    if event.severity in {"warning", "error", "critical"}:
        return True
    if event.category != "unknown":
        return True
    excerpt = event.raw_excerpt.lower()
    return any(
        marker in excerpt
        for marker in (
            "state changed",
            "rby1 model:",
            "model version:",
            "rby1 version:",
            "rby1-sdk version:",
        )
    )


def _parse_log(
    db: Database,
    artifact_id: int,
    source_name: str,
    member_name: str | None,
    stream: BinaryIO,
    cancel_check: Callable[[], None] | None,
    progress_callback: Callable[[int], None] | None,
) -> AnalysisCounts:
    rows: list[EventInsert] = []
    count = 0
    groups: dict[str, int] = {}
    previous: dict[str, float] = {}
    for event in parse_rpc_log(
        stream,
        member_name or source_name,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
        event_filter=_potential_log_event,
    ):
        observations = _assign_observations(event.time_observations, groups, previous)
        if not _interesting(event):
            continue
        primary = next((item for item in observations if item.basis == "log_wall"), None)
        primary = primary or next(iter(observations), None)
        diagnostics = [asdict(item) for item in evaluate_event(event, DEFAULT_RULES)]
        category = event.category
        if category == "unknown" and "version:" in event.raw_excerpt.lower():
            category = "metadata"
        rows.append((
            (
                artifact_id,
                source_name,
                member_name,
                event.line,
                event.byte_offset,
                event.raw_digest,
                event.raw_excerpt,
                event.severity,
                category,
                event.signature,
                event.component,
                None if event.joint is None else str(event.joint),
                event.command,
                event.result,
                primary.value if primary else None,
                primary.basis if primary else None,
                primary.raw if primary else None,
                json.dumps(diagnostics, ensure_ascii=True, separators=(",", ":")),
            ),
            observations,
        ))
        if len(rows) >= 1000:
            count += _insert_events(db, rows)
            rows.clear()
    count += _insert_events(db, rows)
    _persist_correlations(db, artifact_id)
    return AnalysisCounts(events=count)


def _assign_observations(
    observations: tuple[TimeObservation, ...],
    groups: dict[str, int],
    previous: dict[str, float],
) -> tuple[TimeObservation, ...]:
    assigned: list[TimeObservation] = []
    for observation in observations:
        group = groups.setdefault(observation.basis, 0)
        monotonic = True
        if (
            observation.value is not None
            and observation.basis in previous
            and observation.value < previous[observation.basis]
        ):
            group += 1
            groups[observation.basis] = group
            monotonic = False
        if observation.value is not None:
            previous[observation.basis] = observation.value
        assigned.append(
            replace(
                observation,
                discontinuity_group=group,
                monotonic=monotonic,
            )
        )
    return tuple(assigned)


def _insert_events(db: Database, rows: list[EventInsert]) -> int:
    if not rows:
        return 0
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for event_row, observations in rows:
            cursor = connection.execute(
                "INSERT INTO events(artifact_id,source_name,member_name,line,byte_offset,raw_digest,"
                "excerpt,severity,category,signature,component,joint,command,result,time_value,time_basis,"
                "time_raw,diagnostic_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                event_row,
            )
            event_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO time_observations(artifact_id,event_id,basis,value,raw,source_sequence,"
                "precision,timezone_known,parse_status,discontinuity_group,monotonic) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    (
                        event_row[0],
                        event_id,
                        item.basis,
                        item.value,
                        item.raw,
                        item.source_sequence,
                        item.precision,
                        int(item.timezone_known),
                        item.parse_status,
                        item.discontinuity_group,
                        int(item.monotonic),
                    )
                    for item in observations
                ),
            )
        connection.execute("COMMIT")
    return len(rows)


def _compatible_observations(
    request: list[sqlite3.Row], result: list[sqlite3.Row]
) -> tuple[str, float | None] | None:
    priorities = ("log_wall", "log_robot_relative", "request_header_epoch")
    for basis in priorities:
        request_item = next((item for item in request if item["basis"] == basis), None)
        result_item = next((item for item in result if item["basis"] == basis), None)
        if request_item is None or result_item is None:
            continue
        if request_item["discontinuity_group"] != result_item["discontinuity_group"]:
            continue
        delta = None
        if request_item["value"] is not None and result_item["value"] is not None:
            delta = float(result_item["value"] - request_item["value"])
        return basis, delta
    return None


def _persist_correlations(db: Database, artifact_id: int) -> None:
    with db.connect() as connection:
        events = connection.execute(
            "SELECT id,line,component,command,result FROM events "
            "WHERE artifact_id=? ORDER BY line,id",
            (artifact_id,),
        ).fetchall()
        observations: dict[int, list[sqlite3.Row]] = {}
        for item in connection.execute(
            "SELECT event_id,basis,value,discontinuity_group FROM time_observations "
            "WHERE artifact_id=? AND event_id IS NOT NULL ORDER BY source_sequence,id",
            (artifact_id,),
        ):
            observations.setdefault(int(item["event_id"]), []).append(item)

        pending: dict[tuple[str | None, str], sqlite3.Row] = {}
        rows: list[tuple[object, ...]] = []
        for event in events:
            command = event["command"]
            result = event["result"]
            component = event["component"]
            if command and not result:
                pending[(component, command)] = event
                continue
            if not result:
                continue
            request = pending.get((component, command)) if command else None
            if request is None:
                candidates = [
                    item for (candidate_component, _), item in pending.items()
                    if candidate_component == component
                ]
                request = candidates[-1] if candidates else None
            if request is None:
                continue
            pending.pop((request["component"], request["command"]), None)
            explicit = bool(command and command == request["command"])
            compatible = _compatible_observations(
                observations.get(int(request["id"]), []),
                observations.get(int(event["id"]), []),
            )
            basis, delta = compatible or (
                "source_sequence",
                float(event["line"] - request["line"]),
            )
            rows.append(
                (
                    artifact_id,
                    request["id"],
                    event["id"],
                    basis,
                    delta,
                    "high" if explicit else "low",
                    "구성요소와 명령 결과가 명시적으로 일치합니다."
                    if explicit
                    else "기록 순서에 따른 연관 정보이며, 인과관계를 의미하지는 않습니다.",
                    0,
                )
            )
        if rows:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT OR IGNORE INTO correlations(artifact_id,request_event_id,result_event_id,"
                "basis,delta,confidence,explanation,causal) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            connection.execute("COMMIT")


def _series_kind(name: str) -> str:
    lowered = name.lower()
    return "discrete" if lowered.startswith("power_") or "state" in lowered else "continuous"


def _parse_csv(
    db: Database,
    artifact_id: int,
    source_name: str,
    member_name: str | None,
    stream: TextIO,
    cancel_check: Callable[[], None] | None,
    progress_callback: Callable[[int], None] | None,
) -> AnalysisCounts:
    wall_offset, fault_wall = _fault_wall_alignment(stream, cancel_check)
    rows: list[tuple[int, float, str, float, str]] = []
    observation_rows: list[tuple[object, ...]] = []
    samples = 0
    groups: dict[str, int] = {}
    previous: dict[str, float] = {}
    if fault_wall is not None:
        observation_rows.extend(
            _observation_rows(
                artifact_id,
                None,
                _assign_observations((fault_wall,), groups, previous),
            )
        )
    for sample in parse_fault_csv(
        stream,
        member_name or source_name,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    ):
        assigned = _assign_observations(sample.time_observations, groups, previous)
        observation_rows.extend(_observation_rows(artifact_id, None, assigned))
        observation = next(
            (item for item in assigned if item.value is not None),
            None,
        )
        if observation is None:
            continue
        sample_time = observation.value + wall_offset if wall_offset is not None else observation.value
        for name, value in sample.values.items():
            if name.lower() == "timestamp" or not isinstance(value, float):
                continue
            rows.append((artifact_id, sample_time, name, value, _series_kind(name)))
        samples += 1
        if len(rows) >= _CSV_INSERT_BATCH:
            _insert_chart_rows(db, rows)
            rows.clear()
        if len(observation_rows) >= _CSV_INSERT_BATCH:
            _insert_time_observations(db, observation_rows)
            observation_rows.clear()
    _insert_chart_rows(db, rows)
    _insert_time_observations(db, observation_rows)
    return AnalysisCounts(samples=samples)


def _fault_wall_alignment(
    stream: TextIO,
    cancel_check: Callable[[], None] | None,
) -> tuple[float | None, TimeObservation | None]:
                                                                                     
    if not stream.seekable():
        return None, None
    stream.seek(0)
    fault_wall: float | None = None
    fault_wall_observation: TimeObservation | None = None
    last_sample: float | None = None
    bytes_since_check = 0
    for line in stream:
        bytes_since_check += len(line.encode("utf-8", errors="replace"))
        if cancel_check is not None and bytes_since_check >= 10 * 1024 * 1024:
            cancel_check()
            bytes_since_check = 0
        if line.startswith("# Fault Occurred At:"):
            raw = line.split(":", 1)[1].strip()
            parsed = parse_fault_time({"wall_time": raw}, 0)
            fault_wall_observation = parsed[0] if parsed else None
            fault_wall = fault_wall_observation.value if fault_wall_observation else None
        elif line and not line.startswith("#") and not line.lower().startswith("timestamp"):
            try:
                last_sample = float(line.split(",", 1)[0])
            except ValueError:
                continue
    stream.seek(0)
    if fault_wall is None or last_sample is None:
        return None, fault_wall_observation
    return fault_wall - last_sample, fault_wall_observation


def _observation_rows(
    artifact_id: int,
    event_id: int | None,
    observations: tuple[TimeObservation, ...],
) -> list[tuple[object, ...]]:
    return [
        (
            artifact_id,
            event_id,
            item.basis,
            item.value,
            item.raw,
            item.source_sequence,
            item.precision,
            int(item.timezone_known),
            item.parse_status,
            item.discontinuity_group,
            int(item.monotonic),
        )
        for item in observations
    ]


def _insert_time_observations(db: Database, rows: list[tuple[object, ...]]) -> None:
    if not rows:
        return
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "INSERT INTO time_observations(artifact_id,event_id,basis,value,raw,source_sequence,"
            "precision,timezone_known,parse_status,discontinuity_group,monotonic) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.execute("COMMIT")


def _insert_chart_rows(db: Database, rows: list[tuple[int, float, str, float, str]]) -> None:
    if not rows:
        return
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "INSERT INTO chart_samples(artifact_id,sample_time,name,value,kind) VALUES (?,?,?,?,?)",
            rows,
        )
        connection.execute("COMMIT")


def _parse_leaf(
    db: Database,
    artifact_id: int,
    source_name: str,
    member_name: str | None,
    name: str,
    stream: BinaryIO,
    cancel_check: Callable[[], None] | None,
    progress_callback: Callable[[int], None] | None = None,
) -> AnalysisCounts:
    kind = classify(name)
    if kind == ArtifactKind.LOG:
        return _parse_log(
            db,
            artifact_id,
            source_name,
            member_name,
            stream,
            cancel_check,
            progress_callback,
        )
    if kind == ArtifactKind.CSV:
        text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="")
        return _parse_csv(
            db,
            artifact_id,
            source_name,
            member_name,
            text,
            cancel_check,
            progress_callback,
        )
    _warning(db, artifact_id, "unsupported_source", f"Unsupported source: {name}", member_name)
    return AnalysisCounts(warnings=1)


def _retain_member(
    db: Database,
    paths: CasePaths,
    case_id: str,
    source_name: str,
    name: str,
    stream: BinaryIO,
    size_hint: int,
) -> tuple[int, StoredContent, bool]:
    quotas = Quotas()
    require_storage_limits(
        paths.root,
        paths.root.parent,
        paths.temp,
        size_hint,
        quotas=quotas,
    )
    stored = retain_stream(
        stream,
        lambda digest: paths.content("members", digest),
        paths.temp,
        limit=quotas.member_size,
    )
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id,status FROM artifacts WHERE case_id=? AND kind='member' AND sha256=?",
            (case_id, stored.digest),
        ).fetchone()
        is_new = row is None
        previous_status = None if row is None else str(row["status"])
        if row is None:
            cursor = connection.execute(
                "INSERT INTO artifacts(case_id,kind,sha256,size,stored_path,original_name,status) "
                "VALUES (?, 'member', ?, ?, ?, ?, 'stored')",
                (case_id, stored.digest, stored.size, str(stored.path), name),
            )
            artifact_id = int(cursor.lastrowid)
        else:
            artifact_id = int(row["id"])
        provenance = connection.execute(
            "SELECT 1 FROM provenance WHERE artifact_id=? AND original_name=? AND member_name=? "
            "LIMIT 1",
            (artifact_id, source_name, name),
        ).fetchone()
        if provenance is None:
            connection.execute(
                "INSERT INTO provenance(artifact_id,original_name,member_name) VALUES (?,?,?)",
                (artifact_id, source_name, name),
            )
        connection.execute("COMMIT")
    should_parse = is_new or previous_status != "parsed"
    if not is_new and previous_status not in {None, "stored", "parsed"}:
        _reset_artifact_analysis(db, case_id, artifact_id)
    return artifact_id, stored, should_parse


def _analyze_archive(
    db: Database,
    paths: CasePaths,
    case_id: str,
    source_name: str,
    archive_artifact_id: int,
    archive_path,
    archive_name: str,
    parent_member: str | None,
    depth: int,
    budget: _ArchiveBudget,
    cancel_check: Callable[[], None] | None,
    progress_callback: Callable[[int], None] | None,
    status_callback: StatusCallback | None,
) -> AnalysisCounts:
    quotas = Quotas()
    reader = SafeArchiveReader(
        max_expanded=max(0, quotas.expanded_import - budget.expanded),
        max_member=quotas.member_size,
        max_members=quotas.members,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
        progress_interval=64 * 1024,
    )
    counts = AnalysisCounts()
    for member in reader.read(archive_path, source_name=archive_name):
        if cancel_check is not None:
            cancel_check()
        budget.members += 1
        if budget.members > quotas.members:
            raise ArchiveViolation("member_count", member.name)
        member_name = (
            f"{parent_member}!/{member.name}" if parent_member else member.name
        )
        if status_callback is not None:
            status_callback("extracting", member_name)
        member_id, member_stored, should_parse = _retain_member(
            db,
            paths,
            case_id,
            source_name,
            member_name,
            member.stream,
            member.size_hint,
        )
        budget.expanded += member_stored.size
        if budget.expanded > quotas.expanded_import:
            raise ArchiveViolation("expansion_budget", member_name)
        counts.members += 1
        if not should_parse:
            continue
        with member_stored.path.open("rb") as prefix_stream:
            prefix = prefix_stream.read(4)
        kind = classify(member.name, prefix)
        if kind in {
            ArtifactKind.ZIP,
            ArtifactKind.GZIP,
            ArtifactKind.TAR,
            ArtifactKind.TAR_GZIP,
        }:
            if status_callback is not None:
                status_callback("parsing_archive", member_name)
            if depth >= quotas.depth:
                _warning(
                    db,
                    member_id,
                    "nesting_depth",
                    f"Archive nesting exceeds depth {quotas.depth}: {member_name}",
                    member_name,
                )
                with db.connect() as connection:
                    connection.execute(
                        "UPDATE artifacts SET status='partial',detail_code='nesting_depth' "
                        "WHERE id=?",
                        (member_id,),
                    )
                counts.warnings += 1
                continue
            counts.add(
                _analyze_archive(
                    db,
                    paths,
                    case_id,
                    source_name,
                    member_id,
                    member_stored.path,
                    member.name,
                    member_name,
                    depth + 1,
                    budget,
                    cancel_check,
                    None,
                    status_callback,
                )
            )
        else:
            if status_callback is not None:
                phase = "parsing_csv" if kind == ArtifactKind.CSV else "parsing_log"
                status_callback(phase, member_name)
            try:
                with member_stored.path.open("rb") as stream:
                    counts.add(
                        _parse_leaf(
                            db,
                            member_id,
                            source_name,
                            member_name,
                            member.name,
                            stream,
                            cancel_check,
                        )
                    )
            except (csv.Error, OSError, UnicodeError, ValueError) as exc:
                _warning(
                    db,
                    member_id,
                    "parse_failed",
                    f"{type(exc).__name__}: {exc}",
                    member_name,
                )
                with db.connect() as connection:
                    connection.execute(
                        "UPDATE artifacts SET status='partial',detail_code='parse_failed' "
                        "WHERE id=?",
                        (member_id,),
                    )
                counts.warnings += 1
                continue
        with db.connect() as connection:
            connection.execute(
                "UPDATE artifacts SET status='parsed' WHERE id=?", (member_id,)
            )
    for warning in reader.warnings:
        warning_member = (
            f"{parent_member}!/{warning.member}"
            if parent_member and warning.member
            else warning.member
        )
        _warning(
            db,
            archive_artifact_id,
            warning.code,
            str(warning),
            warning_member,
        )
        counts.warnings += 1
    return counts


def analyze_upload(
    db: Database,
    paths: CasePaths,
    case_id: str,
    filename: str,
    stored: StoredContent,
    *,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int], None] | None = None,
    status_callback: StatusCallback | None = None,
) -> AnalysisCounts:
                                                                                                   
    row = _artifact_row(db, case_id, "source", stored.digest)
    artifact_id = int(row["id"])
    if row["status"] == "parsed":
        if progress_callback is not None:
            progress_callback(stored.size)
        return AnalysisCounts(sources=1)
    if row["status"] != "stored":
        _reset_artifact_analysis(db, case_id, artifact_id)

    with stored.path.open("rb") as prefix_stream:
        prefix = prefix_stream.read(4)
    kind = classify(filename, prefix)
    counts = AnalysisCounts(sources=1)
    try:
        if cancel_check is not None:
            cancel_check()
        if kind in {ArtifactKind.LOG, ArtifactKind.CSV}:
            if status_callback is not None:
                phase = "parsing_csv" if kind == ArtifactKind.CSV else "parsing_log"
                status_callback(phase, filename)
            with stored.path.open("rb") as stream:
                counts.add(
                    _parse_leaf(
                        db,
                        artifact_id,
                        filename,
                        None,
                        filename,
                        stream,
                        cancel_check,
                        progress_callback,
                    )
                )
        elif kind in {
            ArtifactKind.ZIP,
            ArtifactKind.GZIP,
            ArtifactKind.TAR,
            ArtifactKind.TAR_GZIP,
        }:
            if status_callback is not None:
                status_callback("parsing_archive", filename)
            counts.add(
                _analyze_archive(
                    db,
                    paths,
                    case_id,
                    filename,
                    artifact_id,
                    stored.path,
                    filename,
                    None,
                    1,
                    _ArchiveBudget(),
                    cancel_check,
                    progress_callback,
                    status_callback,
                )
            )
        else:
            _warning(db, artifact_id, "unsupported_source", f"Unsupported source: {filename}")
            counts.warnings += 1
        with db.connect() as connection:
            connection.execute("UPDATE artifacts SET status='parsed' WHERE id=?", (artifact_id,))
        align_naive_log_wall_times(db)
    except (ArchiveViolation, csv.Error, OSError, UnicodeError, ValueError) as exc:
        code = exc.code if isinstance(exc, ArchiveViolation) else "parse_failed"
        _warning(db, artifact_id, code, str(exc))
        with db.connect() as connection:
            connection.execute(
                "UPDATE artifacts SET status='partial',detail_code=? WHERE id=?",
                (code, artifact_id),
            )
        counts.warnings += 1
    if status_callback is not None:
        status_callback("finalizing", filename)
    if progress_callback is not None:
        progress_callback(stored.size)
    return counts
