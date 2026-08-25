from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from rby1_analyzer.api.deps import bearer_token
from rby1_analyzer.api.routes.incidents import _case_db
from rby1_analyzer.charts import ChartPoint, ChartSeries, DenseWindowError, window_series


router = APIRouter(
    prefix="/api/v3",
    tags=["v3-csvs"],
    dependencies=[Depends(bearer_token)],
)

_MOTOR_STATE_BITS = {
    0: ("FET", "FET ON", "status"),
    1: ("RUN", "제어 ON", "status"),
    2: ("INIT", "초기화 완료", "status"),
    3: ("MOD", "제어 모드", "status"),
    4: ("NON_CTR", "Nonius Error", "diagnostic"),
    5: ("BAT", "Low Battery", "diagnostic"),
    6: ("CALIB", "Calibration Mode", "status"),
    7: ("MT_ERR", "Multiturn Error", "diagnostic"),
    8: ("JAM", "Jam Error", "core_fault"),
    9: ("CUR", "Over Current Error", "core_fault"),
    10: ("BIG", "Big Position Error", "core_fault"),
    11: ("INP", "Big Input Error", "core_fault"),
    12: ("FLT", "FET Driver Fault", "diagnostic"),
    13: ("TMP", "Temperature Error 비트", "diagnostic"),
    14: ("PS1", "Lower Position Limit", "diagnostic"),
    15: ("PS2", "Upper Position Limit", "diagnostic"),
    16: ("SPI_EXT", "SPI Extension 상태", "status"),
    17: ("CUR_BIG", "큰 전류 오류", "diagnostic"),
    18: ("CAN_ERR", "CAN Error", "diagnostic"),
}
_MOTOR_STATE_CONTRACT = {
    "width_bits": 32,
    "core_fault_bits": [8, 9, 10, 11],
    "core_fault_names": ["JAM", "CUR", "BIG", "INP"],
    "reserved_range": "19~31",
    "temperature_note": (
        "TMP 비트와 별도로 Core는 joint_state.temperature 임계값으로 Temperature Error를 판정합니다."
    ),
    "dynamixel_head_note": "Dynamixel로 구성된 Head 관절은 motor_state를 0으로 저장합니다.",
}
_ENUMS = {
    "power": {
        0: ("Unknown", "알 수 없음"),
        1: ("ON", "켜짐"),
        2: ("OFF", "꺼짐"),
    },
    "control_manager": {
        0: ("Idle", "대기"),
        1: ("Enabled", "활성"),
        2: ("MinorFault", "경고 Fault"),
        3: ("MajorFault", "중대 Fault"),
    },
    "control_state": {
        0: ("Idle", "대기"),
        1: ("Executing", "실행 중"),
        2: ("Switching", "전환 중"),
    },
}
_JOINT_NAME = re.compile(
    r"(?:^|_)((?:right|left)_arm_[0-6]|head_[0-2]|torso_[0-5]|"
    r"(?:right|left)_wheel|wheel_(?:fr|fl|rr|rl))(?:_|$)"
)

# Flexible patterns for RBY1 Model and Version in logs
_ROBOT_COMBINED_METADATA = re.compile(
    r"(?:rby1|rb-y1)[_\-\s]*(?P<model>[am])\b.*?(?:model\s*)?version\s*[:=]?\s*v?(?P<version>\d+\.\d+)",
    re.IGNORECASE,
)
_ROBOT_MODEL_ISOLATED = re.compile(
    r"\b(?:rby1|rb-y1)[_\-\s]*(?P<model>[am])\b",
    re.IGNORECASE,
)
_ROBOT_VERSION_ISOLATED = re.compile(
    r"(?:model\s*)?version\s*[:=]?\s*v?(?P<version>\d+\.\d+)",
    re.IGNORECASE,
)

_SUPPORTED_MODEL_VERSIONS: dict[str, list[str]] = {
    "a": ["v1.0", "v1.1", "v1.2"],
    "m": ["v1.0", "v1.1", "v1.2", "v1.3"],
}




def motor_state_definitions() -> list[dict[str, object]]:
    definitions = []
    for bit in range(32):
        name, label, kind = _MOTOR_STATE_BITS.get(
            bit,
            (f"RESERVED_{bit}", "예약 비트", "reserved"),
        )
        definitions.append(
            {
                "bit": bit,
                "value": 1 << bit,
                "name": name,
                "label": label,
                "kind": kind,
                "core_fault": kind == "core_fault",
                "reserved": kind == "reserved",
            }
        )
    return definitions


def decode_motor_state(value: int) -> dict[str, object]:
    raw_value = int(value)
    mask = raw_value & 0xFFFFFFFF
    active_bits = [definition for definition in motor_state_definitions() if mask & int(definition["value"])]
    label = "활성 비트 없음" if not active_bits else ", ".join(str(item["label"]) for item in active_bits)
    return {"value": raw_value, "label": label, "active_bits": active_bits}


def decode_enum_state(series_name: str, value: int) -> dict[str, object]:
    enum_name = _enum_name(series_name)
    mapping = _ENUMS.get(enum_name or "", {})
    name, label = mapping.get(int(value), (f"Unknown({int(value)})", f"알 수 없음({int(value)})"))
    return {"value": int(value), "name": name, "label": label, "active_bits": []}


def system_state_contract(series_names: list[str]) -> dict[str, object]:
    return {
        "series_types": {
            name: enum_name
            for name in series_names
            if (enum_name := _enum_name(name)) is not None
        },
        "definitions": {
            enum_name: [
                {"value": value, "name": name, "label": label}
                for value, (name, label) in values.items()
            ]
            for enum_name, values in _ENUMS.items()
        },
    }


def _enum_name(series_name: str) -> str | None:
    lowered = series_name.lower()
    if lowered.startswith("power_") or lowered.endswith("_power"):
        return "power"
    if lowered == "control_manager_state" or lowered.endswith("_control_manager_state"):
        return "control_manager"
    if lowered == "control_state" or lowered.endswith("_control_state"):
        return "control_state"
    return None


def _state_decoder(series_name: str):
    lowered = series_name.lower()
    if _enum_name(lowered):
        return lambda value: decode_enum_state(series_name, value)
    if lowered.endswith("_state"):
        return decode_motor_state
    return None


def _detected_joints(names: list[str]) -> list[str]:
    joints: set[str] = set()
    for name in names:
        match = _JOINT_NAME.search(name.lower())
        if match:
            joints.add(match.group(1))
    return sorted(joints)


def infer_robot_model(evidence: list[str], series_names: list[str]) -> dict[str, Any]:
    detected_pairs: list[tuple[str, str]] = []
    
    # 1. Look for combined model and version lines
    for text in evidence:
        match = _ROBOT_COMBINED_METADATA.search(text)
        if match:
            model = match.group("model").lower()
            version_str = match.group("version").lower()
            norm_version = version_str if version_str.startswith("v") else f"v{version_str}"
            if norm_version in _SUPPORTED_MODEL_VERSIONS.get(model, []):
                pair = (model, norm_version)
                if pair not in detected_pairs:
                    detected_pairs.append(pair)

    # 2. Look for isolated model or version if combined was not found
    if not detected_pairs:
        found_models: set[str] = set()
        found_versions: set[str] = set()
        for text in evidence:
            m = _ROBOT_MODEL_ISOLATED.search(text)
            if m:
                found_models.add(m.group("model").lower())
            v = _ROBOT_VERSION_ISOLATED.search(text)
            if v:
                v_str = v.group("version").lower()
                norm_v = v_str if v_str.startswith("v") else f"v{v_str}"
                found_versions.add(norm_v)
        if len(found_models) == 1 and len(found_versions) == 1:
            m = next(iter(found_models))
            v = next(iter(found_versions))
            if v in _SUPPORTED_MODEL_VERSIONS.get(m, []):
                detected_pairs.append((m, v))

    # 3. Handle Conflicts (multiple conflicting models/versions)
    if len(detected_pairs) > 1:
        conflict_list = [f"{m.upper()} Type {v.upper()}" for m, v in detected_pairs]
        primary_m, primary_v = detected_pairs[0]
        return {
            "model": primary_m,
            "version": primary_v,
            "confidence": "conflict",
            "reason": f"복수의 로봇 모델/버전이 로그에서 발견되었습니다: {', '.join(conflict_list)}",
            "conflicts": [{"model": m, "version": v} for m, v in detected_pairs],
            "supported_models": _SUPPORTED_MODEL_VERSIONS,
        }

    # 4. Single Detected Model
    if len(detected_pairs) == 1:
        model, version = detected_pairs[0]
        return {
            "model": model,
            "version": version,
            "confidence": "detected",
            "reason": f"RPC 로그에서 {model.upper()} Type {version.upper()} 확인",
            "supported_models": _SUPPORTED_MODEL_VERSIONS,
        }

    # 5. Infer from wheel signals if no log metadata found
    lowered_names = {name.lower() for name in series_names}
    if any(name.startswith(("wheel_fr_", "wheel_fl_", "wheel_rr_", "wheel_rl_")) for name in lowered_names):
        return {
            "model": "m",
            "version": "v1.2",
            "confidence": "inferred",
            "reason": "CSV의 Mecanum wheel 신호에서 M Type으로 추론됨 (버전 미확인, V1.2 기본값)",
            "supported_models": _SUPPORTED_MODEL_VERSIONS,
        }
    if any(name.startswith(("right_wheel_", "left_wheel_")) for name in lowered_names):
        return {
            "model": "a",
            "version": "v1.2",
            "confidence": "inferred",
            "reason": "CSV의 Differential wheel 신호에서 A Type으로 추론됨 (버전 미확인, V1.2 기본값)",
            "supported_models": _SUPPORTED_MODEL_VERSIONS,
        }

    return {
        "model": "a",
        "version": "v1.2",
        "confidence": "assumed",
        "reason": "모델 정보가 없어 A Type V1.2로 가정",
        "supported_models": _SUPPORTED_MODEL_VERSIONS,
    }


def _category(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("power_") or lowered.endswith("_power"):
        return "power"
    if lowered.endswith("motor_state") or lowered.endswith("_state") or lowered in {
        "control_manager_state",
        "control_state",
    }:
        return "state"
    if lowered.endswith("_target_pos") or "_target_" in lowered:
        return "target"
    if lowered.endswith(("_temp", "_temperature", "temp", "temperature")) or "_temp" in lowered or "_temperature" in lowered:
        return "temperature"
    if lowered.endswith("_pos") or lowered.endswith("_position") or "_position" in lowered:
        return "position"
    if lowered.endswith("_cur") or lowered.endswith("_current") or "_current" in lowered:
        return "current"
    if lowered.endswith("_vel") or lowered.endswith("_velocity") or "_velocity" in lowered:
        return "velocity"
    if lowered.endswith(("_tq", "_torque")) or "_tq" in lowered or "_torque" in lowered:
        return "torque"
    return "other"


def _category_metadata(names: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = defaultdict(list)
    for name in names:
        categories[_category(name)].append(name)
    return {key: sorted(value) for key, value in sorted(categories.items())}


def _state_metadata(series: list[ChartSeries]) -> dict[str, list[dict[str, object]]]:
    metadata: dict[str, list[dict[str, object]]] = {}
    for item in series:
        decoder = _state_decoder(item.name)
        if decoder is None:
            continue
        values = sorted({int(point.value) for point in item.points if point.value == int(point.value)})
        metadata[item.name] = [decoder(value) for value in values]
    return metadata


def generate_csv_meta(cases: Any, case_id: str) -> dict[str, Any]:
    paths = cases.paths(case_id)
    meta_file = paths.root / "csv_meta.json"

    db = cases.open(case_id)
    with db.connect() as connection:
        artifact_rows = connection.execute(
            "SELECT a.id,a.original_name,MIN(p.member_name) AS member_name,"
            "MIN(c.sample_time) AS min_sample_time,MAX(c.sample_time) AS max_sample_time,"
            "COUNT(DISTINCT c.sample_time) AS sample_count "
            "FROM artifacts a JOIN chart_samples c ON c.artifact_id=a.id "
            "LEFT JOIN provenance p ON p.artifact_id=a.id "
            "WHERE a.case_id=? GROUP BY a.id,a.original_name ORDER BY a.id",
            (case_id,),
        ).fetchall()
        series_rows = connection.execute(
            "SELECT c.artifact_id,c.name,c.kind,COUNT(*) AS point_count,"
            "MIN(c.sample_time) AS min_sample_time,MAX(c.sample_time) AS max_sample_time "
            "FROM chart_samples c JOIN artifacts a ON a.id=c.artifact_id "
            "WHERE a.case_id=? GROUP BY c.artifact_id,c.name,c.kind ORDER BY c.artifact_id,c.name",
            (case_id,),
        ).fetchall()
        metadata_rows = connection.execute(
            "SELECT e.artifact_id,e.excerpt FROM events e JOIN artifacts a ON a.id=e.artifact_id "
            "WHERE a.case_id=? AND ("
            " e.category='metadata'"
            " OR e.excerpt LIKE '%rby1%'"
            " OR e.excerpt LIKE '%rb-y1%'"
            " OR e.excerpt LIKE '%Model Version%'"
            " OR e.excerpt LIKE '%RBY1 Model%'"
            ") ORDER BY e.id LIMIT 500",
            (case_id,),
        ).fetchall()
        provenance_rows = connection.execute(
            "SELECT p.artifact_id,p.original_name FROM provenance p "
            "JOIN artifacts a ON a.id=p.artifact_id WHERE a.case_id=?",
            (case_id,),
        ).fetchall()
        linked_artifact_rows = connection.execute(
            "SELECT DISTINCT l.artifact_id AS csv_artifact_id,e.artifact_id AS evidence_artifact_id "
            "FROM incident_csv_links l JOIN incidents i ON i.id=l.incident_id "
            "JOIN events e ON e.id=i.primary_event_id WHERE i.case_id=?",
            (case_id,),
        ).fetchall()
        linked_incidents_rows = connection.execute(
            "SELECT l.artifact_id, i.id, i.title, i.severity, i.family, i.summary, "
            "i.start_time, i.start_raw, l.delta_seconds, "
            "CASE WHEN i.family='major_fault' THEN 'major' "
            "     WHEN i.family='minor_fault' THEN 'minor' "
            "     ELSE NULL END AS fault_level "
            "FROM incident_csv_links l JOIN incidents i ON i.id=l.incident_id "
            "WHERE i.case_id=? ORDER BY ABS(l.delta_seconds), i.id",
            (case_id,),
        ).fetchall()

    evidence_by_artifact: dict[int, list[str]] = defaultdict(list)
    for row in metadata_rows:
        evidence_by_artifact[int(row["artifact_id"])].append(str(row["excerpt"]))
    sources_by_artifact: dict[int, set[str]] = defaultdict(set)
    evidence_artifacts_by_source: dict[str, set[int]] = defaultdict(set)
    for row in provenance_rows:
        artifact_id = int(row["artifact_id"])
        source_name = str(row["original_name"])
        sources_by_artifact[artifact_id].add(source_name)
        if artifact_id in evidence_by_artifact:
            evidence_artifacts_by_source[source_name].add(artifact_id)
    linked_evidence_by_csv: dict[int, set[int]] = defaultdict(set)
    for row in linked_artifact_rows:
        linked_evidence_by_csv[int(row["csv_artifact_id"])].add(int(row["evidence_artifact_id"]))
    by_artifact: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in series_rows:
        by_artifact[int(row["artifact_id"])].append(
            {
                "name": row["name"],
                "kind": row["kind"],
                "point_count": int(row["point_count"]),
                "min_sample_time": row["min_sample_time"],
                "max_sample_time": row["max_sample_time"],
            }
        )
    incidents_by_csv: dict[int, list[dict[str, object]]] = defaultdict(list)
    for inc_row in linked_incidents_rows:
        art_id = int(inc_row["artifact_id"])
        delta = float(inc_row["delta_seconds"]) if inc_row["delta_seconds"] is not None else 0.0
        series_items = by_artifact.get(art_id, [])
        csv_max = max((item["max_sample_time"] for item in series_items if item["max_sample_time"] is not None), default=5.0)
        csv_min = min((item["min_sample_time"] for item in series_items if item["min_sample_time"] is not None), default=0.0)
        if csv_min > 1_000_000_000 and inc_row["start_time"] is not None:
            csv_sample_time = float(inc_row["start_time"])
        else:
            csv_sample_time = round(max(csv_min, min(csv_max, csv_max - delta)), 3)
        raw_time = str(inc_row["start_raw"] or "")
        display_log_time = raw_time.split(" ")[-1] if " " in raw_time else raw_time
        incidents_by_csv[art_id].append(
            {
                "id": str(inc_row["id"]),
                "title": str(inc_row["title"]),
                "severity": str(inc_row["severity"]),
                "fault_level": inc_row["fault_level"],
                "summary": str(inc_row["summary"] or ""),
                "start_time": inc_row["start_time"],
                "start_raw": raw_time,
                "log_time_display": display_log_time,
                "delta_seconds": delta,
                "csv_sample_time": csv_sample_time,
                "csv_time_display": f"{csv_sample_time:.3f}s" if csv_min < 1_000_000_000 else display_log_time,
            }
        )
    artifacts = []
    for row in artifact_rows:
        artifact_id = int(row["id"])
        series_meta = by_artifact[artifact_id]
        names = [str(item["name"]) for item in series_meta]
        evidence_artifact_ids = {artifact_id, *linked_evidence_by_csv[artifact_id]}
        for source_name in sources_by_artifact[artifact_id]:
            evidence_artifacts_by_source[source_name].add(artifact_id)
        # If no linked evidence for this CSV, check all metadata in the case
        if not any(evidence_by_artifact[aid] for aid in evidence_artifact_ids):
            for all_aid in evidence_by_artifact:
                evidence_artifact_ids.add(all_aid)
        model_evidence = [
            excerpt
            for evidence_artifact_id in sorted(evidence_artifact_ids)
            for excerpt in evidence_by_artifact[evidence_artifact_id]
        ]
        inferred = infer_robot_model(model_evidence, names)
        artifacts.append(
            {
                "id": artifact_id,
                "name": row["original_name"],
                "member": row["member_name"],
                "min_sample_time": row["min_sample_time"],
                "max_sample_time": row["max_sample_time"],
                "sample_count": int(row["sample_count"]),
                "available_series": series_meta,
                "detected_joints": _detected_joints(names),
                "categories": _category_metadata(names),
                "robot_model": inferred,
                "linked_incidents": incidents_by_csv[artifact_id],
            }
        )
    result = {
        "case_id": case_id,
        "csvs": artifacts,
        "supported_models": _SUPPORTED_MODEL_VERSIONS,
    }
    try:
        meta_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


@router.get("/cases/{case_id}/csvs")
def case_csvs(case_id: str, request: Request) -> dict[str, object]:
    runtime = request.app.state.runtime
    paths = runtime.cases.paths(case_id)
    meta_file = paths.root / "csv_meta.json"
    if meta_file.is_file():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return generate_csv_meta(runtime.cases, case_id)



def _format_datetime_slug(raw: str | None, val: float | None) -> str:
    if raw:
        cleaned = re.sub(r"[^0-9a-zA-Z]", "_", raw).strip("_")
        cleaned = re.sub(r"_+", "_", cleaned)
        if cleaned:
            return cleaned
    if val is not None:
        return f"{val:.1f}s".replace(".", "_")
    return "unknown"


def _format_case_timeline_stem(connection: Any, case_id: str) -> tuple[str, str, str]:
    metadata_rows = connection.execute(
        "SELECT e.excerpt FROM events e JOIN artifacts a ON a.id=e.artifact_id "
        "WHERE a.case_id=? AND ("
        " e.category='metadata'"
        " OR e.excerpt LIKE '%rby1%'"
        " OR e.excerpt LIKE '%rb-y1%'"
        " OR e.excerpt LIKE '%Model Version%'"
        " OR e.excerpt LIKE '%RBY1 Model%'"
        ") ORDER BY e.id LIMIT 100",
        (case_id,),
    ).fetchall()
    model_evidence = [str(r["excerpt"]) for r in metadata_rows]
    inferred = infer_robot_model(model_evidence, [])
    model_name = inferred.get("model")
    model_version = inferred.get("version")
    model_slug = f"{model_name}_{model_version}" if model_name and model_version else (model_name or "rby1_unknown")

    time_bounds = connection.execute(
        "SELECT MIN(e.time_value) as min_val, MAX(e.time_value) as max_val "
        "FROM events e JOIN artifacts a ON a.id=e.artifact_id WHERE a.case_id=?",
        (case_id,),
    ).fetchone()
    first_raw_row = connection.execute(
        "SELECT e.time_raw FROM events e JOIN artifacts a ON a.id=e.artifact_id "
        "WHERE a.case_id=? AND e.time_raw IS NOT NULL ORDER BY e.id ASC LIMIT 1",
        (case_id,),
    ).fetchone()
    last_raw_row = connection.execute(
        "SELECT e.time_raw FROM events e JOIN artifacts a ON a.id=e.artifact_id "
        "WHERE a.case_id=? AND e.time_raw IS NOT NULL ORDER BY e.id DESC LIMIT 1",
        (case_id,),
    ).fetchone()

    first_raw = first_raw_row["time_raw"] if first_raw_row else None
    last_raw = last_raw_row["time_raw"] if last_raw_row else None
    min_val = time_bounds["min_val"] if time_bounds else None
    max_val = time_bounds["max_val"] if time_bounds else None

    first_slug = _format_datetime_slug(first_raw, min_val)
    last_slug = _format_datetime_slug(last_raw, max_val)

    stem = f"{model_slug}_{first_slug}_{last_slug}"
    period = f"{first_raw or (f'{min_val:.1f}s' if min_val is not None else '')} ~ {last_raw or (f'{max_val:.1f}s' if max_val is not None else '')}"
    return stem, model_slug, period


def _ensure_timeline_files(runtime: Any, case_id: str) -> tuple[Path, Path, int, str, str, str]:
    paths = runtime.cases.paths(case_id)
    db = runtime.cases.open(case_id)
    with db.connect() as connection:
        stem, model_slug, period = _format_case_timeline_stem(connection, case_id)
        events = connection.execute(
            "SELECT e.id,e.artifact_id,e.line,e.byte_offset,e.excerpt,e.severity,"
            "e.category,e.component,e.joint,e.command,e.result,e.time_value,e.time_basis,e.time_raw,"
            "a.original_name,a.kind "
            "FROM events e JOIN artifacts a ON a.id=e.artifact_id "
            "WHERE a.case_id=? ORDER BY e.time_value, e.line",
            (case_id,),
        ).fetchall()

    jsonl_file = paths.root / f"{stem}.jsonl"
    log_file = paths.root / f"{stem}.log"
    legacy_jsonl = paths.root / "timeline_consolidated.jsonl"
    legacy_log = paths.root / "timeline_consolidated.log"

    if not jsonl_file.is_file() or jsonl_file.stat().st_size == 0:
        with jsonl_file.open("w", encoding="utf-8") as f:
            for row in events:
                f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        # Keep legacy file in sync
        try:
            legacy_jsonl.write_bytes(jsonl_file.read_bytes())
        except Exception:
            pass

    if not log_file.is_file() or log_file.stat().st_size == 0:
        with log_file.open("w", encoding="utf-8") as f:
            for row in events:
                time_str = row["time_raw"] or (f"{row['time_value']:.3f}s" if row["time_value"] is not None else "")
                src = row["original_name"]
                excerpt = row["excerpt"]
                f.write(f"[{time_str}] [{src}] {excerpt}\n")
        # Keep legacy file in sync
        try:
            legacy_log.write_bytes(log_file.read_bytes())
        except Exception:
            pass

    return jsonl_file, log_file, len(events), stem, model_slug, period


@router.get("/cases/{case_id}/timeline/info")
def case_timeline_info(case_id: str, request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    jsonl_file, log_file, count, stem, model_slug, period = _ensure_timeline_files(runtime, case_id)
    return {
        "case_id": case_id,
        "stem": stem,
        "model": model_slug,
        "period": period,
        "jsonl_path": str(jsonl_file),
        "log_path": str(log_file),
        "filename_jsonl": jsonl_file.name,
        "filename_log": log_file.name,
        "event_count": count,
        "size_bytes_jsonl": jsonl_file.stat().st_size if jsonl_file.is_file() else 0,
        "size_bytes_log": log_file.stat().st_size if log_file.is_file() else 0,
    }


@router.get("/cases/{case_id}/timeline/download")
def download_timeline(case_id: str, request: Request, format: str = "jsonl") -> FileResponse:
    runtime = request.app.state.runtime
    jsonl_file, log_file, _, stem, _, _ = _ensure_timeline_files(runtime, case_id)
    target = log_file if format == "log" else jsonl_file
    media_type = "text/plain" if format == "log" else "application/x-jsonlines"
    return FileResponse(target, media_type=media_type, filename=target.name)




@router.get("/cases/{case_id}/csvs/{artifact_id}/chart")
def csv_artifact_chart(
    case_id: str,
    artifact_id: int,
    request: Request,
    series: Annotated[list[str] | None, Query()] = None,
    start: Annotated[float | None, Query()] = None,
    end: Annotated[float | None, Query()] = None,
    max_points: Annotated[int, Query(ge=4, le=2_000)] = 2_000,
    skip_dense: Annotated[bool, Query()] = False,
) -> dict[str, object]:
    runtime = request.app.state.runtime
    paths = runtime.cases.paths(case_id)
    is_default_query = series is None and start is None and end is None and max_points == 2_000 and skip_dense
    cache_file = paths.root / f"chart_{artifact_id}_cache.json"
    if is_default_query and cache_file.is_file():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    db = _case_db(request, case_id)
    with db.connect() as connection:
        artifact = connection.execute(
            "SELECT id,original_name FROM artifacts WHERE id=? AND case_id=?",
            (artifact_id, case_id),
        ).fetchone()
        if artifact is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "csv artifact not found")
        bounds = connection.execute(
            "SELECT MIN(sample_time) AS start,MAX(sample_time) AS end FROM chart_samples WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if bounds is None or bounds["start"] is None or bounds["end"] is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "csv artifact has no samples")
        window_start = float(bounds["start"] if start is None else start)
        window_end = float(bounds["end"] if end is None else end)
        if window_end <= window_start:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end must be greater than start")
        available_rows = connection.execute(
            "SELECT name,kind FROM chart_samples WHERE artifact_id=? GROUP BY name,kind ORDER BY name",
            (artifact_id,),
        ).fetchall()
        available = {str(item["name"]): str(item["kind"]) for item in available_rows}
        selected = list(dict.fromkeys(name for name in series if name in available)) if series else list(available)
        chart_series: list[ChartSeries] = []
        
        # Batch single-query for high-performance reading
        if series is None or len(selected) > 10:
            rows = connection.execute(
                "SELECT name, sample_time, value FROM chart_samples "
                "WHERE artifact_id=? AND sample_time BETWEEN ? AND ? "
                "ORDER BY name, sample_time",
                (artifact_id, window_start, window_end),
            ).fetchall()
            points_by_name: dict[str, list[ChartPoint]] = defaultdict(list)
            for row in rows:
                points_by_name[str(row["name"])].append(
                    ChartPoint(float(row["sample_time"]), float(row["value"]))
                )
            chart_series = [
                ChartSeries(name, available[name], tuple(points_by_name[name]))
                for name in selected
                if name in available
            ]
        else:
            for name in selected:
                points = connection.execute(
                    "SELECT sample_time,value FROM chart_samples WHERE artifact_id=? AND name=? "
                    "AND sample_time BETWEEN ? AND ? ORDER BY sample_time",
                    (artifact_id, name, window_start, window_end),
                ).fetchall()
                chart_series.append(
                    ChartSeries(
                        name,
                        available[name],
                        tuple(ChartPoint(float(item["sample_time"]), float(item["value"])) for item in points),
                    )
                )
        linked_incidents_rows = connection.execute(
            "SELECT l.artifact_id, i.id, i.title, i.severity, i.family, i.summary, "
            "i.start_time, i.start_raw, l.delta_seconds, "
            "CASE WHEN i.family='major_fault' THEN 'major' "
            "     WHEN i.family='minor_fault' THEN 'minor' "
            "     ELSE NULL END AS fault_level "
            "FROM incident_csv_links l JOIN incidents i ON i.id=l.incident_id "
            "WHERE l.artifact_id=? ORDER BY ABS(l.delta_seconds), i.id",
            (artifact_id,),
        ).fetchall()
    dense_series: list[dict[str, object]] = []
    try:
        reduced = window_series(
            chart_series,
            start=window_start,
            end=window_end,
            selected=set(selected),
            max_points=max_points,
        )
    except DenseWindowError as error:
        if not skip_dense:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "too_dense_requires_zoom",
                    "required_points": error.required_points,
                    "suggested_window_seconds": error.suggested_window_seconds,
                },
            ) from error
        reduced = []
        for item in chart_series:
            try:
                reduced.extend(
                    window_series(
                        [item],
                        start=window_start,
                        end=window_end,
                        selected={item.name},
                        max_points=max_points,
                    )
                )
            except DenseWindowError as series_error:
                dense_series.append(
                    {
                        "name": item.name,
                        "required_points": series_error.required_points,
                        "suggested_window_seconds": series_error.suggested_window_seconds,
                    }
                )
    result = {
        "case_id": case_id,
        "artifact": {"id": int(artifact["id"]), "name": artifact["original_name"]},
        "start": window_start,
        "end": window_end,
        "available_series": list(available),
        "state_metadata": _state_metadata(chart_series),
        "motor_state_bits": motor_state_definitions(),
        "motor_state_contract": _MOTOR_STATE_CONTRACT,
        "system_state_contract": system_state_contract(selected),
        "dense_series": dense_series,
        "linked_incidents": [
            {
                "id": str(r["id"]),
                "title": str(r["title"]),
                "severity": str(r["severity"]),
                "fault_level": r["fault_level"],
                "summary": str(r["summary"] or ""),
                "start_time": r["start_time"],
                "start_raw": str(r["start_raw"] or ""),
                "log_time_display": str(r["start_raw"] or "").split(" ")[-1] if " " in str(r["start_raw"] or "") else str(r["start_raw"] or ""),
                "delta_seconds": float(r["delta_seconds"]) if r["delta_seconds"] is not None else 0.0,
                "csv_sample_time": float(r["start_time"]) if window_start > 1_000_000_000 and r["start_time"] is not None else round(max(window_start, min(window_end, window_end - (float(r["delta_seconds"]) if r["delta_seconds"] is not None else 0.0))), 3),
                "csv_time_display": f"{round(max(window_start, min(window_end, window_end - (float(r['delta_seconds']) if r['delta_seconds'] is not None else 0.0))), 3):.3f}s" if window_start < 1_000_000_000 else (str(r["start_raw"] or "").split(" ")[-1] if " " in str(r["start_raw"] or "") else str(r["start_raw"] or "")),
            }
            for r in linked_incidents_rows
        ],
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
    if is_default_query:
        try:
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return result

