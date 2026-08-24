from __future__ import annotations

import re
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from rby1_analyzer.api.deps import bearer_token
from rby1_analyzer.api.routes.v2 import _case_db
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
_ROBOT_METADATA = re.compile(
    r"RBY1\s+Model:\s*rby1(?P<model>[am])\s*,\s*Model\s+Version:\s*(?P<version>v\d+\.\d+)",
    re.IGNORECASE,
)
_SUPPORTED_MODEL_VERSIONS = {
    "a": {"v1.0", "v1.1", "v1.2"},
    "m": {"v1.0", "v1.1", "v1.2", "v1.3"},
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


def infer_robot_model(evidence: list[str], series_names: list[str]) -> dict[str, str]:
                                                                              
    explicit = [match.groupdict() for text in evidence if (match := _ROBOT_METADATA.search(text))]
    if explicit:
        item = explicit[0]
        model = item["model"].lower()
        version = item["version"].lower()
        if version not in _SUPPORTED_MODEL_VERSIONS[model]:
            return {
                "model": model,
                "version": "v1.2",
                "confidence": "inferred",
                "reason": f"RPC 로그에서 {model.upper()} Type을 확인했으나 {version.upper()} 모델이 없어 V1.2로 대체",
            }
        return {
            "model": model,
            "version": version,
            "confidence": "detected",
            "reason": "RPC 로그의 RBY1 Model / Model Version 항목에서 확인",
        }

    lowered_names = {name.lower() for name in series_names}
    if any(name.startswith(("wheel_fr_", "wheel_fl_", "wheel_rr_", "wheel_rl_")) for name in lowered_names):
        return {
            "model": "m",
            "version": "v1.2",
            "confidence": "inferred",
            "reason": "CSV의 Mecanum wheel 신호에서 M Type으로 추론, 버전은 V1.2로 가정",
        }
    if any(name.startswith(("right_wheel_", "left_wheel_")) for name in lowered_names):
        return {
            "model": "a",
            "version": "v1.2",
            "confidence": "inferred",
            "reason": "CSV의 Differential wheel 신호에서 A Type으로 추론, 버전은 V1.2로 가정",
        }
    return {
        "model": "a",
        "version": "v1.2",
        "confidence": "assumed",
        "reason": "모델 정보가 없어 A Type 정밀구동 헤드 V1.2로 가정",
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
    if lowered.endswith("_pos") or lowered.endswith("_position") or "_position" in lowered:
        return "position"
    if lowered.endswith("_cur") or lowered.endswith("_current") or "_current" in lowered:
        return "current"
    if lowered.endswith("_vel") or lowered.endswith("_velocity") or "_velocity" in lowered:
        return "velocity"
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


@router.get("/cases/{case_id}/csvs")
def case_csvs(case_id: str, request: Request) -> dict[str, object]:
    db = _case_db(request, case_id)
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
            "WHERE a.case_id=? AND (e.category='metadata' OR e.excerpt LIKE '%RBY1 Model:%') "
            "ORDER BY e.id LIMIT 200",
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
    artifacts = []
    for row in artifact_rows:
        artifact_id = int(row["id"])
        series_meta = by_artifact[artifact_id]
        names = [str(item["name"]) for item in series_meta]
        evidence_artifact_ids = {artifact_id, *linked_evidence_by_csv[artifact_id]}
        for source_name in sources_by_artifact[artifact_id]:
            evidence_artifact_ids.update(evidence_artifacts_by_source[source_name])
        model_evidence = [
            excerpt
            for evidence_artifact_id in sorted(evidence_artifact_ids)
            for excerpt in evidence_by_artifact[evidence_artifact_id]
        ]
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
                "robot_model": infer_robot_model(model_evidence, names),
            }
        )
    return {"case_id": case_id, "csvs": artifacts}


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
    return {
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
