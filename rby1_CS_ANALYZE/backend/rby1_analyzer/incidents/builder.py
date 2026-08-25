from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any

from rby1_analyzer.storage.database import Database
from rby1_analyzer.timeline.time import parse_fault_time

from .rules import IncidentRuleMatch, classify_event, extract_entities


class IncidentRebuildBusy(RuntimeError):
    detail_code = "case_analysis_in_progress"

    def __init__(self, case_id: str):
        super().__init__(self.detail_code)
        self.case_id = case_id


@dataclass(slots=True)
class ClassifiedEvent:
    event: dict[str, Any]
    match: IncidentRuleMatch
    joints: tuple[str, ...]
    rails: tuple[str, ...]

    @property
    def id(self) -> int:
        return int(self.event["id"])


@dataclass(slots=True)
class IncidentCluster:
    primary: ClassifiedEvent
    direct: list[ClassifiedEvent] = field(default_factory=list)
    evidence: dict[int, tuple[dict[str, Any], str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.add_direct(self.primary)

    def add_direct(self, item: ClassifiedEvent) -> None:
        self.direct.append(item)
        self.evidence[item.id] = (
            item.event,
            item.match.role,
            "동일 장애 시간대의 직접 오류 또는 상태 근거",
        )
        if self.primary.match.role in {"status", "reaction"} and item.match.role == "root":
            self.primary = item
        elif self.primary.match.rule_id == "unknown_error" and item.match.specificity > 10:
            self.primary = item

    @property
    def first(self) -> ClassifiedEvent:
        return self.direct[0]

    @property
    def last(self) -> ClassifiedEvent:
        return self.direct[-1]


def _sort_key(event: dict[str, Any]) -> tuple[object, ...]:
    basis = event.get("time_basis")
    value = event.get("time_value")
    if basis in {"log_wall", "request_header_epoch"} and value is not None:
        return (0, float(value), int(event["id"]))
    if value is not None:
        return (1, int(event["artifact_id"]), float(value), int(event["id"]))
    return (2, int(event["artifact_id"]), int(event["line"]), int(event["id"]))


def _delta(previous: dict[str, Any], current: dict[str, Any]) -> float | None:
    previous_basis = previous.get("time_basis")
    current_basis = current.get("time_basis")
    previous_value = previous.get("time_value")
    current_value = current.get("time_value")
    if (
        previous_value is not None
        and current_value is not None
        and previous_basis == current_basis
        and (
            previous_basis in {"log_wall", "request_header_epoch"}
            or previous["artifact_id"] == current["artifact_id"]
        )
    ):
        return float(current_value) - float(previous_value)
    return None


def _line_gap(previous: dict[str, Any], current: dict[str, Any]) -> int | None:
    if previous["artifact_id"] != current["artifact_id"]:
        return None
    return int(current["line"]) - int(previous["line"])


def _can_group(cluster: IncidentCluster, item: ClassifiedEvent) -> bool:
    previous = cluster.last
    seconds = _delta(previous.event, item.event)
    lines = _line_gap(previous.event, item.event)
    same_family = cluster.primary.match.family == item.match.family

    if seconds is not None and seconds < -0.001:
        return False
    if lines is not None and lines < 0 and seconds is None:
        return False

    if same_family:
        if cluster.primary.match.family == "unknown_error":
            same_signature = cluster.primary.event.get("signature") == item.event.get("signature")
            if not same_signature:
                return False
        window = max(cluster.primary.match.group_window, item.match.group_window)
        if seconds is not None:
            return seconds <= window
        return lines is not None and lines <= 50

    if item.match.role in {"status", "reaction"}:
        if seconds is not None:
            return seconds <= 3.0
        return lines is not None and lines <= 25

    fault_chain = {
        "joint_state_timeout",
        "joint_readiness_loss",
        "motor_drive_error",
        "power_state_loss",
        "power_command_timeout",
        "power_48v_mismatch",
        "major_fault",
        "minor_fault",
        "control_cancellation_failure",
        "invalid_control_request",
        "singularity",
        "tracking_error",
    }
    if cluster.primary.match.family in fault_chain and item.match.family in fault_chain:
        if seconds is not None:
            return seconds <= 0.75
        return lines is not None and lines <= 12
    return False


def _build_clusters(events: list[dict[str, Any]]) -> list[IncidentCluster]:
    classified: list[ClassifiedEvent] = []
    for event in events:
        match = classify_event(event)
        if match is None:
            continue
        joints, rails = extract_entities(str(event["excerpt"]))
        classified.append(ClassifiedEvent(event, match, joints, rails))

    clusters: list[IncidentCluster] = []
    for item in classified:
        target: IncidentCluster | None = None
        for cluster in reversed(clusters[-50:]):
            if _can_group(cluster, item):
                target = cluster
                break
        if target is None:
            clusters.append(IncidentCluster(item))
        else:
            target.add_direct(item)
    return clusters


def _context_role(event: dict[str, Any]) -> tuple[str, str] | None:
    excerpt = str(event["excerpt"])
    lowered = excerpt.lower()
    category = str(event["category"]).lower()
    if "loop stat" in lowered:
        return "measurement", "오류 직후 기록된 실시간 루프 지연 통계"
    if "robot states have been saved:" in lowered:
        return "reaction", "Fault 발생 후 저장된 상태 CSV 경로"
    if lowered.rstrip().endswith("control canceled"):
        return "fallout", "선행 요청 이후 취소된 제어"
    if "poweroff" in lowered or "power off" in lowered:
        return "reaction", "Fault 이후 실행된 전원 차단 동작"
    if "power command succeeded" in lowered:
        return "reaction", "인접한 전원 명령 처리 결과"
    if category == "state_transition" or "state changed" in lowered:
        return "status", "인접한 상태 전환"
    if "control ended with an unknown reason" in lowered:
        return "fallout", "선행 장애 이후 종료된 제어"
    if event.get("command") and not event.get("result"):
        return "command", "장애 직전에 기록된 명령"
    return None


def _distance_to_cluster(cluster: IncidentCluster, event: dict[str, Any]) -> float | None:
    before = _delta(event, cluster.first.event)
    after = _delta(cluster.last.event, event)
    if before is not None and 0 <= before <= 2.0:
        return before
    if after is not None and 0 <= after <= 3.0:
        return after
    if before is not None or after is not None:
        return None
    first_gap = _line_gap(event, cluster.first.event)
    if first_gap is not None and 0 <= first_gap <= 20:
        return first_gap / 1000
    last_gap = _line_gap(cluster.last.event, event)
    if last_gap is not None and 0 <= last_gap <= 25:
        return last_gap / 1000
    return None


def _attach_context(
    clusters: list[IncidentCluster],
    events: list[dict[str, Any]],
    correlations: list[dict[str, Any]],
) -> None:
    owner: dict[int, IncidentCluster] = {}
    by_id = {int(event["id"]): event for event in events}
    for cluster in clusters:
        for item in cluster.direct:
            owner[item.id] = cluster

    for correlation in correlations:
        result_id = int(correlation["result_event_id"])
        request_id = int(correlation["request_event_id"])
        cluster = owner.get(result_id)
        request = by_id.get(request_id)
        if cluster is None or request is None or request_id in cluster.evidence:
            continue
        cluster.evidence[request_id] = (
            request,
            "command",
            "실패 결과와 명시적으로 연결된 선행 명령",
        )

    linked = set(owner)
    for event in events:
        event_id = int(event["id"])
        if event_id in linked:
            continue
        context = _context_role(event)
        if context is None:
            continue
        nearest: tuple[float, IncidentCluster] | None = None
        for cluster in clusters:
            distance = _distance_to_cluster(cluster, event)
            if distance is None:
                continue
            if nearest is None or distance < nearest[0]:
                nearest = (distance, cluster)
        if nearest is None:
            continue
        role, relation = context
        nearest[1].evidence[event_id] = (event, role, relation)


def _event_range(cluster: IncidentCluster) -> tuple[float | None, float | None, str | None, str | None, str | None]:
    ordered = sorted((item.event for item in cluster.direct), key=_sort_key)
    values = [float(item["time_value"]) for item in ordered if item.get("time_value") is not None]
    start_time = min(values) if values else None
    end_time = max(values) if values else None
    first = ordered[0]
    last = ordered[-1]
    return start_time, end_time, first.get("time_basis"), first.get("time_raw"), last.get("time_raw")


def _entities(cluster: IncidentCluster) -> tuple[list[str], list[str], list[str]]:
    components: set[str] = set()
    joints: set[str] = set()
    rails: set[str] = set()
    for event, _role, _relation in cluster.evidence.values():
        component = str(event.get("component") or "").strip()
        if component:
            components.add(component)
        event_joints, event_rails = extract_entities(str(event["excerpt"]))
        joints.update(event_joints)
        rails.update(event_rails)
    return sorted(components), sorted(joints), sorted(rails)


def _summary(cluster: IncidentCluster, components: list[str], joints: list[str]) -> str:
    assets = joints[:3] or components[:2]
    suffix = ""
    total_assets = len(joints) if joints else len(components)
    if total_assets > len(assets):
        suffix = f" 외 {total_assets - len(assets)}개"
    asset_text = f" · {', '.join(assets)}{suffix}" if assets else ""
    return f"{cluster.primary.match.title}{asset_text} · 근거 {len(cluster.evidence)}건"


def _incident_actions(cluster: IncidentCluster) -> tuple[list[str], list[str], list[str]]:
    checks = list(dict.fromkeys(cluster.primary.match.checks))
    remedies = list(dict.fromkeys(cluster.primary.match.remedies))
    gaps = list(dict.fromkeys(cluster.primary.match.evidence_gaps))
    families = {item.match.family for item in cluster.direct}
    if "realtime_scheduling_delay" in families and cluster.primary.match.family != "realtime_scheduling_delay":
        checks.append("같은 시각 RPC 온도와 CPU 부하를 확인하십시오.")
        gaps.append("RPC 호스트 성능 자료가 있으면 통신 문제와 처리 지연을 구분할 수 있습니다.")
    return checks, remedies, gaps


def _link_fault_csvs(
    connection: sqlite3.Connection,
    incident_rows: list[tuple[int, float | None, float | None, str | None]],
) -> None:
    fault_rows = connection.execute(
        "SELECT t.artifact_id,t.value,t.raw,a.original_name FROM time_observations t "
        "JOIN artifacts a ON a.id=t.artifact_id "
        "WHERE t.basis='fault_wall' AND t.event_id IS NULL AND t.value IS NOT NULL "
        "ORDER BY t.value,t.id"
    ).fetchall()
    for fault in fault_rows:
        fault_time = float(fault["value"])
        artifact_id = int(fault["artifact_id"])
        bounds = connection.execute(
            "SELECT MIN(sample_time) AS min_t, MAX(sample_time) AS max_t FROM chart_samples WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        min_t = float(bounds["min_t"]) if bounds and bounds["min_t"] is not None else 0.0
        max_t = float(bounds["max_t"]) if bounds and bounds["max_t"] is not None else 5.0
        duration = max_t - min_t if max_t > min_t else 5.0

        for incident_id, start, end, _basis in incident_rows:
            if start is None:
                continue
            anchor = end if end is not None else start
            delta = fault_time - float(anchor)
            if -1.0 <= delta <= duration + 1.0:
                csv_timeline_pos = max(min_t, min(max_t, max_t - delta))
                confidence = "high" if 0 <= delta <= duration else "medium"
                connection.execute(
                    "INSERT OR REPLACE INTO incident_csv_links(incident_id,artifact_id,delta_seconds,confidence,reason) "
                    "VALUES (?,?,?,?,?)",
                    (
                        incident_id,
                        artifact_id,
                        delta,
                        confidence,
                        f"CSV 타임라인 {csv_timeline_pos:.3f}s (delta: {delta:+.3f}s)",
                    ),
                )


def ensure_fault_links(db: Database, case_id: str) -> None:
    with db.connect() as connection:
        csv_artifacts = connection.execute(
            "SELECT a.id, a.original_name, p.member_name "
            "FROM artifacts a "
            "LEFT JOIN provenance p ON p.artifact_id=a.id "
            "WHERE a.case_id=? AND (a.original_name LIKE '%.csv' OR p.member_name LIKE '%.csv' OR a.kind='member')",
            (case_id,),
        ).fetchall()

        repaired = False
        for art in csv_artifacts:
            art_id = int(art["id"])
            obs = connection.execute(
                "SELECT 1 FROM time_observations WHERE artifact_id=? AND basis='fault_wall' AND value IS NOT NULL LIMIT 1",
                (art_id,),
            ).fetchone()
            if obs is None:
                filename = str(art["member_name"] or art["original_name"] or "")
                m = re.search(
                    r"(\d{4})[-_](\d{2})[-_](\d{2})[_\sT-](\d{2})[-_:](\d{2})[-_:](\d{2})(?:[-_.](\d{1,6}))?",
                    filename,
                )
                if m:
                    millis = m.group(7) or "000"
                    iso_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}.{millis}"
                    parsed = parse_fault_time({"wall_time": iso_str}, 0)
                    if parsed and parsed[0].value is not None:
                        val = parsed[0].value
                        connection.execute(
                            "INSERT INTO time_observations(artifact_id,basis,value,raw,source_sequence,precision,timezone_known,parse_status) "
                            "VALUES (?, 'fault_wall', ?, ?, 0, 'decimal', 0, 'parsed')",
                            (art_id, val, iso_str),
                        )
                        repaired = True

        link_count = connection.execute(
            "SELECT COUNT(*) AS count FROM incident_csv_links l "
            "JOIN incidents i ON i.id=l.incident_id WHERE i.case_id=?",
            (case_id,),
        ).fetchone()

        if repaired or link_count is None or link_count["count"] == 0:
            incident_rows = connection.execute(
                "SELECT id, start_time, end_time, time_basis FROM incidents WHERE case_id=?",
                (case_id,),
            ).fetchall()
            incident_tuples = [
                (int(r["id"]), r["start_time"], r["end_time"], r["time_basis"])
                for r in incident_rows
            ]
            if incident_tuples:
                _link_fault_csvs(connection, incident_tuples)


def rebuild_incidents(db: Database, case_id: str, *, job_id: str | None = None) -> int:
                                                                                 
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            active_sql = (
                "SELECT id FROM jobs WHERE case_id=? AND state IN ('queued','running','cancel_requested')"
            )
            active_params: list[object] = [case_id]
            if job_id is not None:
                active_sql += " AND id<>?"
                active_params.append(job_id)
            active = connection.execute(active_sql + " LIMIT 1", active_params).fetchone()
            if active is not None:
                raise IncidentRebuildBusy(case_id)

            rows = connection.execute(
                "SELECT e.*,a.case_id,a.sha256 AS artifact_sha256,a.kind AS artifact_kind "
                "FROM events e JOIN artifacts a ON a.id=e.artifact_id "
                "WHERE a.case_id=? ORDER BY e.id",
                (case_id,),
            ).fetchall()
            correlations = connection.execute(
                "SELECT c.* FROM correlations c JOIN artifacts a ON a.id=c.artifact_id "
                "WHERE a.case_id=? ORDER BY c.id",
                (case_id,),
            ).fetchall()
            events = [dict(row) for row in rows]
            events.sort(key=_sort_key)
            clusters = _build_clusters(events)
            _attach_context(clusters, events, [dict(row) for row in correlations])

            started_at = datetime.now(timezone.utc).isoformat()
            connection.execute("DELETE FROM analysis_runs WHERE case_id=?", (case_id,))
            cursor = connection.execute(
                "INSERT INTO analysis_runs(case_id,job_id,schema_version,started_at) "
                "VALUES (?,?,2,?)",
                (case_id, job_id, started_at),
            )
            run_id = int(cursor.lastrowid)
            persisted: list[tuple[int, float | None, float | None, str | None]] = []
            for cluster in clusters:
                start, end, basis, start_raw, end_raw = _event_range(cluster)
                components, joints, rails = _entities(cluster)
                checks, remedies, gaps = _incident_actions(cluster)
                direct_occurrences = sum(
                    item.match.role not in {"status", "reaction"} for item in cluster.direct
                ) or 1
                primary = cluster.primary.match
                incident = connection.execute(
                    "INSERT INTO incidents(run_id,case_id,family,title,severity,primary_event_id,"
                    "start_time,end_time,time_basis,start_raw,end_raw,meaning,summary,confidence,"
                    "confidence_reason,occurrence_count,event_count,affected_components,affected_joints,"
                    "affected_power_rails,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        case_id,
                        primary.family,
                        primary.title,
                        primary.severity,
                        cluster.primary.id,
                        start,
                        end,
                        basis,
                        start_raw,
                        end_raw,
                        primary.meaning,
                        _summary(cluster, components, joints),
                        primary.confidence,
                        primary.confidence_reason,
                        direct_occurrences,
                        len(cluster.evidence),
                        json.dumps(components, ensure_ascii=False),
                        json.dumps(joints, ensure_ascii=False),
                        json.dumps(rails, ensure_ascii=False),
                        started_at,
                    ),
                )
                incident_id = int(incident.lastrowid)
                persisted.append((incident_id, start, end, basis))
                ordered_evidence = sorted(
                    cluster.evidence.values(), key=lambda item: _sort_key(item[0])
                )
                connection.executemany(
                    "INSERT INTO incident_events(incident_id,event_id,role,rank,relation) "
                    "VALUES (?,?,?,?,?)",
                    (
                        (incident_id, int(event["id"]), role, rank, relation)
                        for rank, (event, role, relation) in enumerate(ordered_evidence, 1)
                    ),
                )
                connection.executemany(
                    "INSERT INTO incident_hypotheses(incident_id,rank,text,confidence,rationale,"
                    "source_rule_id) VALUES (?,?,?,?,?,?)",
                    (
                        (
                            incident_id,
                            rank,
                            cause,
                            primary.confidence,
                            primary.confidence_reason,
                            primary.rule_id,
                        )
                        for rank, cause in enumerate(primary.causes, 1)
                    ),
                )
                action_rows = [
                    (incident_id, "check", rank, text, primary.rule_id)
                    for rank, text in enumerate(checks, 1)
                ]
                action_rows.extend(
                    (incident_id, "remedy", rank, text, primary.rule_id)
                    for rank, text in enumerate(remedies, 1)
                )
                action_rows.extend(
                    (incident_id, "collect", rank, text, primary.rule_id)
                    for rank, text in enumerate(gaps, 1)
                )
                connection.executemany(
                    "INSERT INTO incident_actions(incident_id,kind,priority,text,source_rule_id) "
                    "VALUES (?,?,?,?,?)",
                    action_rows,
                )
            _link_fault_csvs(connection, persisted)
            connection.execute(
                "UPDATE analysis_runs SET completed_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), run_id),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return len(clusters)
