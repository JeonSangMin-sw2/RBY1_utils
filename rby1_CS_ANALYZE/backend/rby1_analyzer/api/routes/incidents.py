from __future__ import annotations

from collections import defaultdict
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from rby1_analyzer.api.deps import bearer_token
from rby1_analyzer.charts import ChartPoint, ChartSeries, DenseWindowError, window_series
from rby1_analyzer.incidents.builder import IncidentRebuildBusy, ensure_fault_links, rebuild_incidents


router = APIRouter(
    prefix="/api/v2",
    tags=["v2-incidents"],
    dependencies=[Depends(bearer_token)],
)


def _case_db(request: Request, case_id: str):
    try:
        return request.app.state.runtime.cases.open(case_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found") from None


def _ensure_incidents(db, case_id: str) -> None:
    with db.connect() as connection:
        run = connection.execute(
            "SELECT 1 FROM analysis_runs WHERE case_id=? AND completed_at IS NOT NULL LIMIT 1",
            (case_id,),
        ).fetchone()
        events = connection.execute(
            "SELECT 1 FROM events e JOIN artifacts a ON a.id=e.artifact_id "
            "WHERE a.case_id=? LIMIT 1",
            (case_id,),
        ).fetchone()
    if run is None and events is not None:
        try:
            rebuild_incidents(db, case_id)
        except IncidentRebuildBusy:
            return
    try:
        ensure_fault_links(db, case_id)
    except Exception:
        pass


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


_LAYER_ORDER = {
    "motor": 0,
    "control_manager": 1,
    "hardware": 2,
    "control_interface": 3,
    "service_api": 4,
    "other": 5,
}
_LAYER_LABELS = {
    "motor": "모터 / 조인트",
    "control_manager": "Control Manager",
    "hardware": "하드웨어",
    "control_interface": "Control Interface",
    "service_api": "서비스 / API",
    "other": "기타",
}
_MOTOR_LAYER_FAMILIES = {
    "arm6_wakeup_failure",
    "joint_operation_failure",
    "joint_position_limit",
    "joint_readiness_loss",
    "joint_state_timeout",
    "motor_communication_loss",
    "motor_drive_error",
    "tracking_error",
}
_INCIDENT_CONTEXT_SECONDS = 120.0
_INCIDENT_CONTEXT_AFTER_SECONDS = 3.0
_INCIDENT_TIMELINE_LIMIT = 2_000


def _issue_layer(family: str, component: str | None) -> str:
    if family in _MOTOR_LAYER_FAMILIES:
        return "motor"
    normalized = (component or "").lower().replace("-", "_").replace(" ", "_")
    if "controlmanager" in normalized or "control_manager" in normalized:
        return "control_manager"
    if "hardware" in normalized:
        return "hardware"
    if "controlinterface" in normalized or "control_interface" in normalized:
        return "control_interface"
    if "service_api" in normalized or "serviceapi" in normalized or "service" in normalized:
        return "service_api"
    return "other"


def _layer_counts(rows) -> list[dict[str, object]]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[_issue_layer(str(row["family"]), row["component"])] += 1
    return [
        {"layer": layer, "label": _LAYER_LABELS[layer], "count": count}
        for layer, count in sorted(counts.items(), key=lambda item: (_LAYER_ORDER[item[0]], item[0]))
    ]


def _timeline_role(event: dict[str, object]) -> tuple[str, str]:
    category = str(event.get("category") or "").lower()
    severity = str(event.get("severity") or "").lower()
    excerpt = str(event.get("excerpt") or "")
    lowered = excerpt.lower()
    if category == "command" or (event.get("command") and not event.get("result")):
        return "command", "사건 발생 전 2분 이내에 기록된 명령"
    if category == "success":
        return "result_success", "선행 명령 또는 처리 단계의 성공 결과"
    if category in {"failure", "timeout"}:
        return "result_failure", "선행 명령 또는 처리 단계의 실패 결과"
    if category in {"majorfault", "minorfault", "state_transition"} or "state changed" in lowered:
        return "status", "사건 전후에 기록된 상태 전환"
    if category == "operation":
        return "reaction", "사건 전후에 기록된 제어 처리 단계"
    if severity in {"error", "critical"}:
        return "symptom", "사건 발생 전 2분 이내에 기록된 오류"
    if severity == "warning":
        return "warning", "사건 발생 전 2분 이내에 기록된 주의 로그"
    return "context", "사건 발생 전 2분 이내에 기록된 관련 로그"


def _timeline_sort_key(event: dict[str, object]) -> tuple[object, ...]:
    value = event.get("time_value")
    if value is not None:
        return (0, float(value), int(event["line"]), int(event["id"]))
    return (1, int(event["line"]), int(event["id"]))


@router.get("/cases/{case_id}/overview")
def case_overview(case_id: str, request: Request) -> dict[str, object]:
    db = _case_db(request, case_id)
    _ensure_incidents(db, case_id)
    with db.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS count,MIN(start_time) AS first_time,MAX(end_time) AS last_time,"
            "SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS critical_count,"
            "SUM(CASE WHEN family IN ('major_fault','minor_fault') OR EXISTS ("
            "SELECT 1 FROM incident_events ie JOIN events e ON e.id=ie.event_id "
            "WHERE ie.incident_id=incidents.id AND lower(e.category) IN ('majorfault','minorfault')"
            ") THEN 1 ELSE 0 END) AS fault_count,"
            "SUM(CASE WHEN severity='error' THEN 1 ELSE 0 END) AS error_count,"
            "SUM(CASE WHEN family='unknown_error' THEN 1 ELSE 0 END) AS unknown_count "
            "FROM incidents WHERE case_id=?",
            (case_id,),
        ).fetchone()
        first_incident = connection.execute(
            "SELECT start_raw FROM incidents WHERE case_id=? AND start_time IS NOT NULL "
            "ORDER BY start_time ASC,id ASC LIMIT 1",
            (case_id,),
        ).fetchone()
        last_incident = connection.execute(
            "SELECT end_raw FROM incidents WHERE case_id=? AND end_time IS NOT NULL "
            "ORDER BY end_time DESC,id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        severity = connection.execute(
            "SELECT severity,COUNT(*) AS count FROM incidents WHERE case_id=? "
            "GROUP BY severity ORDER BY count DESC,severity",
            (case_id,),
        ).fetchall()
        families = connection.execute(
            "SELECT family,title,COUNT(*) AS count,SUM(occurrence_count) AS occurrences "
            "FROM incidents WHERE case_id=? GROUP BY family,title ORDER BY count DESC,title",
            (case_id,),
        ).fetchall()
        layer_rows = connection.execute(
            "SELECT i.family,e.component FROM incidents i "
            "JOIN events e ON e.id=i.primary_event_id WHERE i.case_id=?",
            (case_id,),
        ).fetchall()
        assets = connection.execute(
            "SELECT affected_components,affected_joints FROM incidents WHERE case_id=?",
            (case_id,),
        ).fetchall()
        source_count = connection.execute(
            "SELECT COUNT(*) AS count FROM artifacts WHERE case_id=? AND kind='source'",
            (case_id,),
        ).fetchone()
        warning_count = connection.execute(
            "SELECT COUNT(*) AS count FROM warnings w LEFT JOIN artifacts a ON a.id=w.artifact_id "
            "WHERE a.case_id=? OR w.artifact_id IS NULL",
            (case_id,),
        ).fetchone()
        csv_links = connection.execute(
            "SELECT COUNT(DISTINCT incident_id) AS count FROM incident_csv_links l "
            "JOIN incidents i ON i.id=l.incident_id WHERE i.case_id=?",
            (case_id,),
        ).fetchone()
        raw_events = connection.execute(
            "SELECT COUNT(*) AS count FROM events e JOIN artifacts a ON a.id=e.artifact_id "
            "WHERE a.case_id=?",
            (case_id,),
        ).fetchone()
    components: set[str] = set()
    joints: set[str] = set()
    for row in assets:
        components.update(_json_list(row["affected_components"]))
        joints.update(_json_list(row["affected_joints"]))
    incident_count = int(total["count"] if total else 0)
    return {
        "case_id": case_id,
        "source_count": int(source_count["count"] if source_count else 0),
        "raw_event_count": int(raw_events["count"] if raw_events else 0),
        "incident_count": incident_count,
        "critical_count": int(total["critical_count"] or 0) if total else 0,
        "fault_count": int(total["fault_count"] or 0) if total else 0,
        "error_count": int(total["error_count"] or 0) if total else 0,
        "unknown_count": int(total["unknown_count"] or 0) if total else 0,
        "first_time": total["first_time"] if total else None,
        "last_time": total["last_time"] if total else None,
        "first_raw": first_incident["start_raw"] if first_incident else None,
        "last_raw": last_incident["end_raw"] if last_incident else None,
        "warning_count": int(warning_count["count"] if warning_count else 0),
        "csv_linked_count": int(csv_links["count"] if csv_links else 0),
        "csv_coverage": (int(csv_links["count"]) / incident_count) if incident_count else 0.0,
        "affected_components": sorted(components),
        "affected_joints": sorted(joints),
        "severity_counts": [dict(row) for row in severity],
        "family_counts": [dict(row) for row in families],
        "layer_counts": _layer_counts(layer_rows),
    }


@router.get("/cases/{case_id}/incidents")
def case_incidents(
    case_id: str,
    request: Request,
    q: Annotated[str | None, Query(max_length=200)] = None,
    severity: Annotated[str | None, Query(max_length=32)] = None,
    family: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 5000,
) -> dict[str, object]:
    db = _case_db(request, case_id)
    _ensure_incidents(db, case_id)
    clauses = ["i.case_id=?"]
    values: list[object] = [case_id]
    if severity:
        clauses.append("i.severity=?")
        values.append(severity)
    if family:
        clauses.append("i.family=?")
        values.append(family)
    if q:
        clauses.append(
            "(i.title LIKE ? OR i.summary LIKE ? OR i.affected_components LIKE ? "
            "OR i.affected_joints LIKE ?)"
        )
        needle = f"%{q}%"
        values.extend((needle, needle, needle, needle))
    values.append(limit)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT i.*,"
            "CASE WHEN i.family='major_fault' OR EXISTS ("
            "SELECT 1 FROM incident_events ie JOIN events e ON e.id=ie.event_id "
            "WHERE ie.incident_id=i.id AND lower(e.category)='majorfault'"
            ") THEN 'major' WHEN i.family='minor_fault' OR EXISTS ("
            "SELECT 1 FROM incident_events ie JOIN events e ON e.id=ie.event_id "
            "WHERE ie.incident_id=i.id AND lower(e.category)='minorfault'"
            ") THEN 'minor' ELSE NULL END AS fault_level,"
            "(SELECT component FROM events e WHERE e.id=i.primary_event_id) AS primary_component,"
            "(SELECT text FROM incident_hypotheses h WHERE h.incident_id=i.id ORDER BY rank,id LIMIT 1) "
            "AS primary_cause,"
            "(SELECT text FROM incident_actions x WHERE x.incident_id=i.id AND x.kind='check' "
            "ORDER BY priority,id LIMIT 1) AS primary_check,"
            "EXISTS(SELECT 1 FROM incident_csv_links l WHERE l.incident_id=i.id) AS csv_linked "
            "FROM incidents i WHERE " + " AND ".join(clauses) + " "
            "ORDER BY CASE WHEN i.start_time IS NULL THEN 1 ELSE 0 END,i.start_time,i.id LIMIT ?",
            values,
        ).fetchall()
    incidents = []
    for row in rows:
        item = dict(row)
        primary_component = item.pop("primary_component", None)
        item["id"] = str(item["id"])
        item["primary_event_id"] = str(item["primary_event_id"])
        item["affected_components"] = _json_list(item["affected_components"])
        item["affected_joints"] = _json_list(item["affected_joints"])
        item["affected_power_rails"] = _json_list(item["affected_power_rails"])
        item["csv_linked"] = bool(item["csv_linked"])
        item["layer"] = _issue_layer(str(item["family"]), primary_component)
        incidents.append(item)
    return {"incidents": incidents}


@router.get("/cases/{case_id}/incidents/{incident_id}")
def case_incident(case_id: str, incident_id: int, request: Request) -> dict[str, object]:
    db = _case_db(request, case_id)
    _ensure_incidents(db, case_id)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM incidents WHERE id=? AND case_id=?",
            (incident_id, case_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        primary_event = connection.execute(
            "SELECT artifact_id,member_name,line,time_value,time_basis FROM events WHERE id=?",
            (row["primary_event_id"],),
        ).fetchone()
        hypotheses = connection.execute(
            "SELECT rank,text,confidence,rationale,source_rule_id FROM incident_hypotheses "
            "WHERE incident_id=? ORDER BY rank,id",
            (incident_id,),
        ).fetchall()
        actions = connection.execute(
            "SELECT kind,priority,text,source_rule_id FROM incident_actions "
            "WHERE incident_id=? ORDER BY CASE kind WHEN 'check' THEN 1 WHEN 'remedy' THEN 2 ELSE 3 END,"
            "priority,id",
            (incident_id,),
        ).fetchall()
        evidence = connection.execute(
            "SELECT ie.role,ie.rank,ie.relation,e.id,e.source_name,e.member_name,e.line,e.byte_offset,"
            "e.raw_digest,e.excerpt,e.severity,e.category,e.component,e.joint,e.command,e.result,"
            "e.time_value,e.time_basis,e.time_raw,a.sha256 AS artifact_sha256 "
            "FROM incident_events ie JOIN events e ON e.id=ie.event_id "
            "JOIN artifacts a ON a.id=e.artifact_id WHERE ie.incident_id=? ORDER BY ie.rank",
            (incident_id,),
        ).fetchall()
        context_rows = []
        context_truncated = False
        if (
            primary_event is not None
            and primary_event["time_value"] is not None
            and primary_event["time_basis"] is not None
        ):
            anchor = float(
                row["start_time"]
                if row["start_time"] is not None
                else primary_event["time_value"]
            )
            end_time = float(row["end_time"] if row["end_time"] is not None else anchor)
            context_rows = connection.execute(
                "SELECT NULL AS role,NULL AS rank,NULL AS relation,e.id,e.source_name,e.member_name,"
                "e.line,e.byte_offset,e.raw_digest,e.excerpt,e.severity,e.category,e.component,e.joint,"
                "e.command,e.result,e.time_value,e.time_basis,e.time_raw,a.sha256 AS artifact_sha256 "
                "FROM events e JOIN artifacts a ON a.id=e.artifact_id "
                "WHERE e.artifact_id=? AND COALESCE(e.member_name,'')=COALESCE(?, '') "
                "AND e.time_basis=? AND e.time_value BETWEEN ? AND ? "
                "ORDER BY e.time_value DESC,e.line DESC,e.id DESC LIMIT ?",
                (
                    primary_event["artifact_id"],
                    primary_event["member_name"],
                    primary_event["time_basis"],
                    anchor - _INCIDENT_CONTEXT_SECONDS,
                    end_time + _INCIDENT_CONTEXT_AFTER_SECONDS,
                    _INCIDENT_TIMELINE_LIMIT + 1,
                ),
            ).fetchall()
        elif primary_event is not None:
            first_line = max(1, int(primary_event["line"]) - _INCIDENT_TIMELINE_LIMIT)
            context_rows = connection.execute(
                "SELECT NULL AS role,NULL AS rank,NULL AS relation,e.id,e.source_name,e.member_name,"
                "e.line,e.byte_offset,e.raw_digest,e.excerpt,e.severity,e.category,e.component,e.joint,"
                "e.command,e.result,e.time_value,e.time_basis,e.time_raw,a.sha256 AS artifact_sha256 "
                "FROM events e JOIN artifacts a ON a.id=e.artifact_id "
                "WHERE e.artifact_id=? AND COALESCE(e.member_name,'')=COALESCE(?, '') "
                "AND e.line BETWEEN ? AND ? ORDER BY e.line DESC,e.id DESC LIMIT ?",
                (
                    primary_event["artifact_id"],
                    primary_event["member_name"],
                    first_line,
                    int(primary_event["line"]) + 100,
                    _INCIDENT_TIMELINE_LIMIT + 1,
                ),
            ).fetchall()
        if len(context_rows) > _INCIDENT_TIMELINE_LIMIT:
            context_rows = context_rows[:_INCIDENT_TIMELINE_LIMIT]
            context_truncated = True
        csv_links = connection.execute(
            "SELECT l.artifact_id,l.delta_seconds,l.confidence,l.reason,a.original_name,a.sha256,"
            "(SELECT GROUP_CONCAT(name, '|') FROM (SELECT DISTINCT name FROM chart_samples "
            "WHERE artifact_id=a.id ORDER BY name)) AS series_names "
            "FROM incident_csv_links l JOIN artifacts a ON a.id=l.artifact_id "
            "WHERE l.incident_id=? ORDER BY ABS(l.delta_seconds),l.artifact_id",
            (incident_id,),
        ).fetchall()
        provenance_rows = connection.execute(
            "SELECT DISTINCT e.id,p.original_name,p.member_name FROM incident_events ie "
            "JOIN events e ON e.id=ie.event_id LEFT JOIN provenance p ON p.artifact_id=e.artifact_id "
            "WHERE ie.incident_id=? ORDER BY e.id,p.id",
            (incident_id,),
        ).fetchall()
    incident = dict(row)
    incident["id"] = str(incident["id"])
    incident["primary_event_id"] = str(incident["primary_event_id"])
    incident["affected_components"] = _json_list(incident["affected_components"])
    incident["affected_joints"] = _json_list(incident["affected_joints"])
    incident["affected_power_rails"] = _json_list(incident["affected_power_rails"])
    provenance: dict[int, list[dict[str, object]]] = defaultdict(list)
    for item in provenance_rows:
        if item["original_name"] is not None:
            provenance[int(item["id"])].append(
                {"original_name": item["original_name"], "member_name": item["member_name"]}
            )
    grouped_actions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in actions:
        grouped_actions[str(item["kind"])].append(dict(item))
    evidence_items = [
        {
            **dict(item),
            "id": str(item["id"]),
            "provenance": provenance.get(int(item["id"]), []),
        }
        for item in evidence
    ]
    timeline_by_id: dict[str, dict[str, object]] = {}
    for item in context_rows:
        timeline_item = dict(item)
        role, relation = _timeline_role(timeline_item)
        timeline_item["id"] = str(timeline_item["id"])
        timeline_item["role"] = role
        timeline_item["relation"] = relation
        timeline_item["provenance"] = []
        timeline_by_id[str(timeline_item["id"])] = timeline_item
    for item in evidence_items:
        timeline_by_id[str(item["id"])] = item
    timeline = sorted(timeline_by_id.values(), key=_timeline_sort_key)
    for rank, item in enumerate(timeline, 1):
        item["rank"] = rank
    return {
        "incident": incident,
        "hypotheses": [dict(item) for item in hypotheses],
        "checks": grouped_actions["check"],
        "remedies": grouped_actions["remedy"],
        "evidence_gaps": grouped_actions["collect"],
        "evidence": evidence_items,
        "timeline": timeline,
        "timeline_context_seconds": _INCIDENT_CONTEXT_SECONDS,
        "timeline_truncated": context_truncated,
        "csv_links": [
            {
                **dict(item),
                "series_names": str(item["series_names"] or "").split("|")
                if item["series_names"]
                else [],
            }
            for item in csv_links
        ],
    }


@router.get("/cases/{case_id}/incidents/{incident_id}/chart")
def incident_chart(
    case_id: str,
    incident_id: int,
    request: Request,
    series: Annotated[list[str] | None, Query()] = None,
    window_seconds: Annotated[float, Query(gt=0.1, le=120.0)] = 5.0,
    max_points: Annotated[int, Query(ge=4, le=2_000)] = 2_000,
) -> dict[str, object]:
    db = _case_db(request, case_id)
    _ensure_incidents(db, case_id)
    with db.connect() as connection:
        incident = connection.execute(
            "SELECT start_time,end_time,primary_event_id FROM incidents WHERE id=? AND case_id=?",
            (incident_id, case_id),
        ).fetchone()
        if incident is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        link = connection.execute(
            "SELECT artifact_id,delta_seconds,confidence,reason FROM incident_csv_links "
            "WHERE incident_id=? ORDER BY ABS(delta_seconds),artifact_id LIMIT 1",
            (incident_id,),
        ).fetchone()
        anchor = incident["start_time"]
        if anchor is None:
            event = connection.execute(
                "SELECT time_value FROM events WHERE id=?", (incident["primary_event_id"],)
            ).fetchone()
            anchor = event["time_value"] if event else None
        if link is None or anchor is None:
            return {
                "matched": False,
                "anchor": anchor,
                "start": None,
                "end": None,
                "series": [],
                "message": "사건 시각과 일치하는 Fault CSV가 확인되지 않았습니다.",
            }

        bounds = connection.execute(
            "SELECT MIN(sample_time) AS min_t, MAX(sample_time) AS max_t FROM chart_samples WHERE artifact_id=?",
            (link["artifact_id"],),
        ).fetchone()
        if bounds is None or bounds["min_t"] is None or bounds["max_t"] is None:
            return {
                "matched": False,
                "anchor": anchor,
                "start": None,
                "end": None,
                "series": [],
                "message": "연결된 Fault CSV에 샘플 데이터가 없습니다.",
            }

        min_t = float(bounds["min_t"])
        max_t = float(bounds["max_t"])

        if min_t > 1_000_000_000:
            start = float(anchor) - window_seconds
            end = float(anchor) + window_seconds
            chart_anchor = float(anchor)
        else:
            delta = float(link["delta_seconds"]) if link["delta_seconds"] is not None else 0.0
            chart_anchor = round(max(min_t, min(max_t, max_t - delta)), 3)
            start = max(min_t, chart_anchor - window_seconds)
            end = min(max_t, chart_anchor + window_seconds)

        available_rows = connection.execute(
            "SELECT name,kind FROM chart_samples WHERE artifact_id=? GROUP BY name,kind ORDER BY name",
            (link["artifact_id"],),
        ).fetchall()
        available = {str(item["name"]): str(item["kind"]) for item in available_rows}
        selected = set(series) if series else None
        names = sorted(selected & available.keys()) if selected else list(available)[:16]
        chart_series: list[ChartSeries] = []
        for name in names:
            points = connection.execute(
                "SELECT sample_time,value FROM chart_samples WHERE artifact_id=? AND name=? "
                "AND sample_time BETWEEN ? AND ? ORDER BY sample_time",
                (link["artifact_id"], name, start, end),
            ).fetchall()
            chart_series.append(
                ChartSeries(
                    name,
                    available[name],
                    tuple(ChartPoint(float(item["sample_time"]), float(item["value"])) for item in points),
                )
            )
    try:
        reduced = window_series(
            chart_series,
            start=start,
            end=end,
            selected=set(names),
            max_points=max_points,
        )
    except DenseWindowError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "too_dense_requires_zoom",
                "required_points": error.required_points,
                "suggested_window_seconds": error.suggested_window_seconds,
            },
        ) from error
    return {
        "matched": True,
        "anchor": chart_anchor,
        "start": start,
        "end": end,
        "csv": dict(link),
        "available_series": list(available),
        "series": [
            {
                "name": item.name,
                "kind": item.kind,
                "nan_count": item.nan_count,
                "points": [[point.time, point.value] for point in item.points],
            }
            for item in reduced
        ],
    }


@router.post("/cases/{case_id}/incidents/rebuild")
def rebuild_case_incidents(case_id: str, request: Request) -> dict[str, object]:
    db = _case_db(request, case_id)
    try:
        count = rebuild_incidents(db, case_id)
    except IncidentRebuildBusy as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": exc.detail_code, "message": "가져오기가 끝난 뒤 다시 시도하십시오."},
        ) from exc
    return {"case_id": case_id, "incident_count": count}
