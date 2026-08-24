from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


JOINT_NAME = re.compile(
    r"\b(?:right|left)_arm_[0-6]\b|\bhead_[0-2]\b|\btorso_[0-5]\b|"
    r"\b(?:right|left)_wheel\b",
    re.IGNORECASE,
)
POWER_RAIL = re.compile(r"(?<!\d)(5v|12v|24v|48v)(?!\d)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IncidentRuleMatch:
    rule_id: str
    family: str
    title: str
    meaning: str
    severity: str
    role: str
    confidence: str
    confidence_reason: str
    causes: tuple[str, ...]
    checks: tuple[str, ...]
    remedies: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    group_window: float = 1.0
    specificity: int = 50


def extract_entities(excerpt: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    joints = tuple(dict.fromkeys(match.group(0).lower() for match in JOINT_NAME.finditer(excerpt)))
    rails = tuple(dict.fromkeys(match.group(1).lower() for match in POWER_RAIL.finditer(excerpt)))
    return joints, rails


def _match(
    *,
    rule_id: str,
    family: str,
    title: str,
    meaning: str,
    severity: str = "error",
    role: str = "root",
    confidence: str = "medium",
    confidence_reason: str = "로그의 명시적 오류 문구와 알려진 장애 패턴이 일치합니다.",
    causes: tuple[str, ...] = (),
    checks: tuple[str, ...] = (),
    remedies: tuple[str, ...] = (),
    evidence_gaps: tuple[str, ...] = (),
    group_window: float = 1.0,
    specificity: int = 50,
) -> IncidentRuleMatch:
    return IncidentRuleMatch(
        rule_id,
        family,
        title,
        meaning,
        severity,
        role,
        confidence,
        confidence_reason,
        causes,
        checks,
        remedies,
        evidence_gaps,
        group_window,
        specificity,
    )


def classify_event(event: Mapping[str, object]) -> IncidentRuleMatch | None:
    excerpt = str(event.get("excerpt") or "")
    lowered = excerpt.lower()
    severity = str(event.get("severity") or "info").lower()
    category = str(event.get("category") or "unknown").lower()
    result = str(event.get("result") or "").lower()
    joints, _rails = extract_entities(excerpt)

                                                                              
    if "servo_on_command.timeout" in lowered and "requested:" in lowered:
        return None
    if "requested: request_header" in lowered and re.search(r"timeout\s*\{\s*\}", lowered):
        return None
    if "unlimited mode enabled, skipping limit check" in lowered:
        return None
    if "control was preempted or canceled by another request" in lowered:
        return None
    if "control was preempted (or cancelled) by another control" in lowered:
        return None
    if "loop stat" in lowered or re.search(r"\bcontrol manager stat(?:\s*\(|:)", lowered):
        return None
    if "robot states have been saved:" in lowered:
        return None
    if lowered.rstrip().endswith("control canceled") and severity in {"info", "debug"}:
        return None
    if "control ended with an unknown reason" in lowered:
        return None

    if "major fault reaction started" in lowered:
        return _match(
            rule_id="major_fault_reaction",
            family="major_fault",
            title="MajorFault 대응 절차 시작",
            meaning="Control Manager가 MajorFault 대응 절차를 시작했습니다.",
            severity="critical",
            role="reaction",
            confidence="high",
            causes=("이 행은 원인보다 앞선 장애에 대한 후속 반응일 가능성이 높습니다.",),
            checks=("같은 시각 직전의 최초 오류와 상태 변화를 확인하십시오.",),
            remedies=("선행 원인을 확인한 뒤 Fault 복구 절차를 수행하십시오.",),
            evidence_gaps=("이 행만으로 MajorFault의 최초 원인은 확정할 수 없습니다.",),
            specificity=95,
        )
    if "minor fault reaction started" in lowered:
        return _match(
            rule_id="minor_fault_reaction",
            family="minor_fault",
            title="MinorFault 대응 절차 시작",
            meaning="Control Manager가 MinorFault 대응 절차를 시작했습니다.",
            severity="warning",
            role="reaction",
            confidence="high",
            causes=("이 행은 선행 제어 오류에 대한 후속 반응일 가능성이 높습니다.",),
            checks=("같은 시각 직전의 제어 오류를 확인하십시오.",),
            remedies=("선행 원인을 제거한 뒤 Control Manager를 복구하십시오.",),
            evidence_gaps=("이 행만으로 MinorFault의 최초 원인은 확정할 수 없습니다.",),
            specificity=95,
        )
    if "control manager state changed to 'majorfault'" in lowered or category == "majorfault":
        return _match(
            rule_id="major_fault_state",
            family="major_fault",
            title="Control Manager MajorFault",
            meaning="Control Manager 상태가 MajorFault로 전환되었습니다.",
            severity="critical",
            role="status",
            confidence="high",
            causes=("직전 하드웨어, 통신, 전원 또는 제어 오류가 MajorFault 전환을 유발했을 가능성이 있습니다.",),
            checks=("직전 3초의 최초 error 로그와 영향을 받은 축을 확인하십시오.",),
            remedies=("선행 장애를 해소한 뒤 정해진 Fault 복구 절차를 수행하십시오.",),
            evidence_gaps=("상태 전환 자체는 최초 원인을 설명하지 않습니다.",),
            specificity=90,
        )
    if "control manager state changed to 'minorfault'" in lowered or category == "minorfault":
        return _match(
            rule_id="minor_fault_state",
            family="minor_fault",
            title="Control Manager MinorFault",
            meaning="Control Manager 상태가 MinorFault로 전환되었습니다.",
            severity="warning",
            role="status",
            confidence="high",
            causes=("직전 제어 오류, Singularity 또는 유효하지 않은 명령이 MinorFault 전환과 연관되었을 가능성이 있습니다.",),
            checks=("직전 3초의 control error와 명령 내용을 확인하십시오.",),
            remedies=("원인이 된 명령이나 자세를 수정한 뒤 Control Manager를 복구하십시오.",),
            evidence_gaps=("상태 전환 자체는 최초 원인을 설명하지 않습니다.",),
            specificity=90,
        )

    state_timeout = re.search(r"timeout:\s*joint\s*['\"]?([^'\" ]+).*state update exceeded timeout", lowered)
    if state_timeout:
        return _match(
            rule_id="joint_state_update_timeout",
            family="joint_state_timeout",
            title="관절 상태 갱신 시간 초과",
            meaning="해당 관절의 상태 업데이트가 설정된 200 ms 기준 안에 들어오지 않았습니다.",
            severity="critical" if "majorfault" in lowered else "error",
            confidence="medium",
            confidence_reason="상태 미수신은 확인되지만, 단선·보드·부하 중 어느 원인인지는 추가 확인이 필요합니다.",
            causes=(
                "CAN 통신이 불안정하거나 해당 모터/보드가 응답하지 않았을 가능성이 있습니다.",
                "RPC의 실시간 처리 지연으로 상태 갱신이 늦어졌을 가능성이 있습니다.",
            ),
            checks=(
                "CAN선 상태를 확인하십시오. Power ON 상태에서 모터를 움직였을 때 엔코더 값이 변하는지 확인하십시오.",
                "같은 시각 다른 관절 timeout과 realtime loop 지연 로그가 함께 발생했는지 확인하십시오.",
            ),
            remedies=("통신 구간과 커넥터를 분리 점검한 뒤 Servo On을 다시 시도하십시오.",),
            evidence_gaps=("모터 전원, CAN 프레임 수신 및 호스트 부하 정보가 있으면 원인 범위를 좁힐 수 있습니다.",),
            group_window=1.0,
            specificity=100,
        )

    no_data = "no data received" in lowered or "no data coming" in lowered or "no data comming" in lowered
    motor_init = "initialize motor" in lowered or "initializing motor" in lowered
    if no_data or (motor_init and any(word in lowered for word in ("failed", "timeout", "error"))):
        arm6 = {"right_arm_6", "left_arm_6"}
        if joints and set(joints).issubset(arm6):
            return _match(
                rule_id="isolated_arm6_servo_on_failure",
                family="arm6_wakeup_failure",
                title="Arm 6축 Servo On 실패",
                meaning="Arm 6축에서만 초기화 데이터가 수신되지 않았습니다.",
                confidence="medium",
                confidence_reason="Arm 6축 단독 미수신 패턴과 일치하지만 하드웨어 확인 전 원인은 추정입니다.",
                causes=(
                    "보드가 정상적으로 깨어나지 않았을 가능성이 있습니다.",
                    "코인셀이 방전되었을 가능성이 있습니다.",
                    "WY2 커넥터의 접촉 상태가 불량할 가능성이 있습니다.",
                    "납땜부 쇼트가 있을 가능성이 있습니다.",
                ),
                checks=(
                    "Preset 명령을 실행해 보십시오.",
                    "코인셀 전압을 확인하십시오.",
                    "WY2 커넥터의 접촉 상태를 확인하십시오.",
                    "납땜부에 쇼트가 있는지 확인하십시오.",
                ),
                remedies=("문제가 반복되면 커넥터, 코인셀 또는 관련 보드의 교체 점검을 진행하십시오.",),
                evidence_gaps=("Preset 전후 결과와 코인셀 실측 전압이 필요합니다.",),
                group_window=30.0,
                specificity=110,
            )
        return _match(
            rule_id="motor_communication_no_data",
            family="motor_communication_loss",
            title="모터 초기화 데이터 미수신",
            meaning="Servo On 과정에서 표시된 모터들의 응답 데이터가 수신되지 않았습니다.",
            confidence="medium",
            confidence_reason="미수신 대상은 확인되지만 CAN 배선, 전원, 보드 중 원인 구분이 필요합니다.",
            causes=("CAN 통신이 불안정할 가능성이 있습니다.",),
            checks=(
                "CAN선 상태를 확인하십시오. Power ON 상태에서 모터를 움직였을 때 엔코더 값이 변하는지 확인하십시오.",
                "미수신 축이 한 축, 한 팔 전체 또는 전 로봇인지 범위를 확인하십시오.",
            ),
            remedies=("문제 구간의 커넥터와 하네스를 분리 점검한 뒤 Servo On을 다시 시도하십시오.",),
            evidence_gaps=("CAN 프레임 수신 여부와 모터 전원 실측값이 필요합니다.",),
            group_window=30.0,
            specificity=100,
        )

    motor_error = re.search(r"\b(big|jam|cur|input|temp(?:erature)?)\s+error\b", lowered)
    if motor_error:
        raw_code = motor_error.group(1).upper()
        code = "TEMP" if raw_code == "TEMPERATURE" else raw_code
        descriptions = {
            "BIG": "모터 드라이브에서 BIG 오류 비트가 보고되었습니다.",
            "JAM": "모터가 목표를 따라가지 못하고 걸림 상태로 판단되었습니다.",
            "CUR": "모터 전류 관련 보호 오류가 보고되었습니다.",
            "INPUT": "모터 입력 또는 참조값 관련 오류가 보고되었습니다.",
            "TEMP": "모터 온도 보호 오류가 보고되었습니다.",
        }
        causes = {
            "BIG": ("모터 내부에서 복합 또는 중대한 오류 상태가 발생했을 가능성이 있습니다.",),
            "JAM": ("기구 간섭, 과부하, 브레이크 또는 구동부 걸림이 발생했을 가능성이 있습니다.",),
            "CUR": ("과부하, 급격한 명령 또는 전류 센싱 이상이 발생했을 가능성이 있습니다.",),
            "INPUT": ("허용되지 않은 참조값 또는 입력 신호 이상이 발생했을 가능성이 있습니다.",),
            "TEMP": ("연속 고부하 또는 냉각 불량으로 모터 온도가 상승했을 가능성이 있습니다.",),
        }
        return _match(
            rule_id=f"motor_{code.lower()}_error",
            family="motor_drive_error",
            title=f"모터 {code} 오류",
            meaning=descriptions[code],
            confidence="medium",
            confidence_reason=f"{code} 오류 비트는 확인되지만 물리 원인은 상태값과 기구 점검이 필요합니다.",
            causes=causes[code],
            checks=(
                "표시된 축의 기구 간섭, 발열, 소음 및 수동 움직임을 확인하십시오.",
                "Fault CSV에서 해당 축의 전류, 위치, 목표 위치 변화를 확인하십시오.",
            ),
            remedies=("부하와 간섭을 제거한 뒤 오류를 초기화하고 저속으로 재현 여부를 확인하십시오.",),
            evidence_gaps=("해당 축의 전류·온도·목표값과 발생 직전 명령이 필요합니다.",),
            group_window=1.0,
            specificity=105,
        )

    if "motor error occurred" in lowered:
        return _match(
            rule_id="motor_error_reported",
            family="motor_drive_error",
            title="모터 오류 발생",
            meaning="하드웨어 계층에서 하나 이상의 모터 오류가 보고되었습니다.",
            confidence="low",
            confidence_reason="모터 오류 발생은 확인되지만 이 행에는 오류 비트와 대상 축이 없습니다.",
            causes=("모터 드라이브 보호 오류, 과부하 또는 통신 이상이 발생했을 가능성이 있습니다.",),
            checks=(
                "같은 시각 직전의 BIG/JAM/CUR/INPUT/TEMP 오류와 영향 축을 확인하십시오.",
                "Fault CSV에서 각 축의 state, current, position 및 target position을 확인하십시오.",
            ),
            remedies=("세부 모터 오류와 기구 상태를 확인한 뒤 원인을 제거하고 Fault를 복구하십시오.",),
            evidence_gaps=("이 행만으로는 대상 축과 세부 오류 비트를 확인할 수 없습니다.",),
            group_window=1.0,
            specificity=85,
        )

    if re.search(r"joint\b.*(?:'ready'|'power')\s+is down", lowered) or "all joint power and ready is down" in lowered:
        return _match(
            rule_id="joint_ready_power_down",
            family="joint_readiness_loss",
            title="관절 Ready/Power 상태 해제",
            meaning="표시된 관절의 Ready 또는 Power 상태가 내려갔습니다.",
            severity="critical",
            confidence="medium",
            confidence_reason="상태 해제는 확인되지만 전원 차단, CAN 손실, 모터 Fault 중 선행 원인 확인이 필요합니다.",
            causes=(
                "모터 Fault 또는 CAN 통신 손실로 Ready 상태가 해제되었을 가능성이 있습니다.",
                "전원 상태 변화가 먼저 발생했을 가능성이 있습니다.",
            ),
            checks=(
                "가장 먼저 Ready/Power가 내려간 축과 직전 모터 오류를 확인하십시오.",
                "같은 시각 전원 상태 변화와 Fault CSV를 확인하십시오.",
            ),
            remedies=("선행 축 또는 전원 문제를 해소한 뒤 Servo On을 다시 수행하십시오.",),
            evidence_gaps=("로그 순서와 Fault CSV 전원/모터 상태가 필요합니다.",),
            group_window=1.0,
            specificity=95,
        )

    if "power state" in lowered and "changed during enable state" in lowered:
        return _match(
            rule_id="power_state_changed_during_enable",
            family="power_state_loss",
            title="Enable 중 전원 상태 변경",
            meaning="Control Manager Enable 상태에서 감시하던 전원 상태가 변경되었습니다.",
            severity="critical",
            confidence="high",
            confidence_reason="전원 상태 변경과 Enable 상태의 충돌이 로그에 명시되어 있습니다.",
            causes=("PDU/CAN 통신 문제 또는 실제 전원 상태 변화가 발생했을 가능성이 있습니다.",),
            checks=(
                "Fault CSV에서 5V/12V/24V/48V 중 어느 상태가 먼저 변했는지 확인하십시오.",
                "PDU CAN 배선과 같은 시각의 전원 명령 로그를 확인하십시오.",
            ),
            remedies=("전원 및 PDU 통신을 안정화한 뒤 Control Manager를 다시 Enable하십시오.",),
            evidence_gaps=("로그의 state 번호만으로는 변한 전원 레일을 확정하기 어렵습니다.",),
            group_window=1.0,
            specificity=105,
        )

    if "emo error" in lowered and "check emo switch" in lowered:
        return _match(
            rule_id="emo_switch_error",
            family="emo_active",
            title="EMO 스위치 활성",
            meaning="EMO 회로가 해제되지 않은 상태로 감지되었습니다.",
            confidence="high",
            confidence_reason="EMO 확인 요청이 오류 메시지에 직접 명시되어 있습니다.",
            causes=("EMO가 눌려 있습니다.",),
            checks=("EMO가 눌려 있는지 확인하십시오.",),
            remedies=("안전 상태를 확인한 뒤 EMO를 해제하고 Reset 절차를 수행하십시오.",),
            evidence_gaps=(),
            specificity=120,
        )

    if "power command failed" in lowered and "unmatched power states" in lowered and "48v" in lowered:
        return _match(
            rule_id="power_48v_state_unmatched_timeout",
            family="power_48v_mismatch",
            title="48V 전원 명령 상태 불일치",
            meaning="48V 전원 명령 후 기대 상태와 PDU 보고 상태가 제한 시간 안에 일치하지 않았습니다.",
            confidence="medium",
            confidence_reason="상태 불일치는 확인되지만 EMO·SCB·FORT 중 어느 조건인지는 추가 확인이 필요합니다.",
            causes=("EMO, SCB Reset 신호 부재 또는 FORT 리모컨 배터리 방전 가능성이 있습니다.",),
            checks=(
                "EMO가 눌려 있는지 확인하십시오.",
                "FORT를 사용하는 경우 리모컨 배터리 상태를 확인하십시오.",
                "SCB Reset 신호를 전송해 보십시오.",
            ),
            remedies=("안전 입력과 Reset 조건을 정상화한 뒤 48V 전원 명령을 다시 수행하십시오.",),
            evidence_gaps=("EMO/SCB/FORT 입력 상태가 없으면 원인을 하나로 확정할 수 없습니다.",),
            group_window=10.0,
            specificity=120,
        )

    if "target position at index" in lowered and "exceeds the maximum allowed bound" in lowered:
        return _match(
            rule_id="target_position_max_bound_exceeded",
            family="target_position_limit",
            title="Target position 허용 범위 초과",
            meaning="명령한 관절 목표 위치가 해당 인덱스의 허용 상한을 초과하여 요청이 거부되었습니다.",
            confidence="high",
            confidence_reason="명령값과 허용 상한이 오류 메시지에 직접 기록되어 있습니다.",
            causes=("target position이 허용 범위 초과",),
            checks=("해당 축 목표 값 제한하여 다시 제어나 명령",),
            remedies=("허용 범위 안으로 목표값을 수정한 후 명령을 다시 전송하십시오.",),
            evidence_gaps=(),
            specificity=125,
        )

    position_limit = re.search(
        r"position .*?(?:exceeds the (?:upper|lower) limit|is (?:below|above) the (?:lower|upper) limit)",
        lowered,
    )
    if "enabling process aborted" in lowered and position_limit:
        return _match(
            rule_id="joint_position_limit_enable_abort",
            family="joint_position_limit",
            title="현재 관절 위치 제한 초과",
            meaning="현재 관절 위치가 지정 범위를 벗어나 Control Manager Enable이 중단되었습니다.",
            confidence="high",
            confidence_reason="현재 위치, 제한값 및 Enable 중단이 로그에 명시되어 있습니다.",
            causes=("해당 축이 지정된 범위를 넘어갔습니다.",),
            checks=("해당 축의 현재 위치와 모델별 joint limit을 확인하십시오.",),
            remedies=("해당 팔을 안전한 범위 내로 옮기거나 Unlimit을 통해 Control Manager를 Enable하십시오.",),
            evidence_gaps=(),
            specificity=125,
        )

    if "robot enabling process failed due to joint limit violations" in lowered:
        return _match(
            rule_id="joint_limit_violations_enable_failure",
            family="joint_position_limit",
            title="관절 위치 제한으로 Enable 실패",
            meaning="하나 이상의 관절이 지정 범위를 벗어나 로봇 Enable 절차가 실패했습니다.",
            confidence="high",
            causes=("해당 축이 지정된 범위를 넘어갔습니다.",),
            checks=("인접 로그에서 제한을 초과한 관절과 현재 위치를 확인하십시오.",),
            remedies=("해당 팔을 안전한 범위 내로 옮기거나 Unlimit을 통해 Control Manager를 Enable하십시오.",),
            evidence_gaps=("이 행만으로는 제한을 초과한 축을 확인할 수 없습니다.",),
            group_window=5.0,
            specificity=115,
        )

    if "singular" in lowered:
        return _match(
            rule_id="cartesian_singularity",
            family="singularity",
            title="Cartesian 제어 Singularity",
            meaning="제어기 내부 reference joint state의 Jacobian 조작성 지표가 설정 임계값을 넘었습니다.",
            severity="warning",
            confidence="high",
            confidence_reason="Singularity 판정이 제어 오류에 직접 포함되어 있습니다.",
            causes=("Cartesian 목표 경로가 특이점에 가까운 reference 자세를 생성했을 가능성이 있습니다.",),
            checks=(
                "Command feedback의 manipulability 값과 설정 threshold를 확인하십시오.",
                "발생 직전 Cartesian target과 reference joint state를 확인하십시오.",
            ),
            remedies=(
                "목표 간격과 속도/가속도 제한을 낮추고 다른 경로로 재계획하십시오.",
                "필요하면 충분히 검증한 뒤 manipulability threshold를 점진적으로 조정하십시오.",
            ),
            evidence_gaps=("발생 시점의 manipulability feedback이 있으면 임계값 접근 과정을 확인할 수 있습니다.",),
            specificity=110,
        )

    if "invalid reference value" in lowered:
        return _match(
            rule_id="invalid_reference_value",
            family="invalid_reference",
            title="유효하지 않은 Reference 값",
            meaning="제어기에 전달된 reference 값이 허용 형식 또는 범위를 만족하지 못했습니다.",
            confidence="high",
            causes=("NaN/Inf, 차원 불일치 또는 허용 범위를 벗어난 reference가 전달되었을 가능성이 있습니다.",),
            checks=("오류 직전 명령의 position, velocity, acceleration, gain 및 행렬 값을 확인하십시오.",),
            remedies=("입력값의 유한성, 차원 및 제한 범위를 검증한 뒤 명령을 다시 보내십시오.",),
            evidence_gaps=("오류 메시지에 필드명이 없다면 직전 요청 payload가 필요합니다.",),
            specificity=105,
        )

    if (
        "hardware is not ready for receiving command" in lowered
        or "not ready for send command" in lowered
        or "motor is not ready to execute" in lowered
        or re.search(r"motor\([^)]+\) is not ready to execute", lowered)
    ):
        return _match(
            rule_id="hardware_not_ready_for_command",
            family="hardware_not_ready",
            title="하드웨어 명령 수신 준비 안 됨",
            meaning="모터 또는 하드웨어가 명령을 받을 Ready 상태가 아니어서 제어가 거부되었습니다.",
            confidence="high",
            causes=("Servo On, 모터 Ready 또는 Control Manager 상태가 명령 실행 조건을 만족하지 않았습니다.",),
            checks=("표시된 축의 Power/Ready 상태와 Servo On 및 Control Manager 상태를 확인하십시오.",),
            remedies=("선행 하드웨어 또는 Fault 상태를 복구한 뒤 올바른 순서로 명령을 다시 수행하십시오.",),
            evidence_gaps=(),
            group_window=10.0,
            specificity=110,
        )

    control_manager_rejected = (
        "control activation aborted:" in lowered
        or "enable operation aborted:" in lowered
        or "disable operation aborted:" in lowered
        or re.search(r"failed to (?:reset|disable|enable) control manager", lowered)
        or ("failed to" in lowered and "pid gain" in lowered and "control manager" in lowered)
        or ("failed to execute 'servo_on'" in lowered and "control manager" in lowered)
        or ("failed to set preset position" in lowered and "control manager" in lowered)
    )
    if control_manager_rejected:
        return _match(
            rule_id="control_manager_operation_rejected",
            family="control_manager_state_rejection",
            title="Control Manager 상태 조건 불일치",
            meaning="현재 Control Manager 또는 활성 제어 상태가 요청 작업의 선행 조건과 달라 작업이 거부되었습니다.",
            confidence="high",
            causes=("Fault/Idle/Enabled 상태 또는 활성 제어 상태와 요청한 작업 순서가 맞지 않습니다.",),
            checks=(
                "오류 문구에 표시된 현재 Control Manager 상태와 요구 상태를 비교하십시오.",
                "진행 중인 Control과 Fault reaction이 남아 있는지 확인하십시오.",
            ),
            remedies=("요구 상태를 먼저 구성하고 진행 중인 제어를 종료한 뒤 작업을 다시 수행하십시오.",),
            evidence_gaps=(),
            group_window=30.0,
            specificity=108,
        )

    if "priority" in lowered and "not higher than" in lowered:
        return _match(
            rule_id="control_priority_rejected",
            family="invalid_control_request",
            title="Control 우선순위 부족",
            meaning="새 제어의 우선순위가 현재 또는 대기 중인 제어보다 높지 않아 활성화되지 않았습니다.",
            confidence="high",
            causes=("새 command의 priority가 기존 command를 선점할 조건을 만족하지 못했습니다.",),
            checks=("새 command와 현재 활성/대기 command의 priority 값을 비교하십시오.",),
            remedies=("제어 우선순위를 조정하거나 기존 제어를 정상 종료한 뒤 다시 요청하십시오.",),
            evidence_gaps=(),
            specificity=115,
        )

    if "command is not set" in lowered:
        return _match(
            rule_id="command_not_set",
            family="invalid_control_request",
            title="제어 Command 미설정",
            meaning="실행 요청에 유효한 command가 설정되지 않았습니다.",
            confidence="high",
            causes=("Command builder 또는 요청 payload에 실행할 제어 명령이 포함되지 않았습니다.",),
            checks=("전송 직전 command builder와 생성된 요청 payload를 확인하십시오.",),
            remedies=("유효한 command를 설정한 뒤 요청을 다시 보내십시오.",),
            evidence_gaps=(),
            specificity=115,
        )

    if "grpc call error:" in lowered:
        return _match(
            rule_id="grpc_request_error",
            family="invalid_control_request",
            title="gRPC 요청 값 오류",
            meaning="gRPC 요청 처리 중 값 범위 또는 키 조회 오류가 발생했습니다.",
            confidence="high",
            causes=("요청 값이 허용 범위를 벗어났거나 존재하지 않는 키를 사용했습니다.",),
            checks=("오류 직전 gRPC 요청의 필드 값, 범위 및 파라미터 키를 확인하십시오.",),
            remedies=("유효한 값과 존재하는 키로 요청을 수정한 뒤 다시 수행하십시오.",),
            evidence_gaps=(),
            specificity=110,
        )

    if "reset denied: fault reaction is still active" in lowered:
        return _match(
            rule_id="fault_reset_rejected_while_reacting",
            family="control_manager_state_rejection",
            title="Fault Reset 요청 거부",
            meaning="Fault 대응 절차가 아직 진행 중이어서 Reset 요청이 거부되었습니다.",
            confidence="high",
            causes=("Major/MinorFault reaction이 완료되기 전에 Reset을 요청했습니다.",),
            checks=("Fault reaction 종료 여부와 현재 Control Manager 상태를 확인하십시오.",),
            remedies=("Fault reaction이 끝난 뒤 선행 원인을 해소하고 Reset을 다시 요청하십시오.",),
            evidence_gaps=(),
            group_window=10.0,
            specificity=115,
        )

    if "joint command is already in progress" in lowered:
        return _match(
            rule_id="joint_command_already_in_progress",
            family="invalid_control_request",
            title="Joint command 중복 실행",
            meaning="이전 Joint command가 진행 중인 상태에서 새 Joint command가 요청되었습니다.",
            confidence="high",
            causes=("완료되지 않은 Joint command와 새 요청이 중복되었습니다.",),
            checks=("현재 Joint command의 완료 또는 취소 상태를 확인하십시오.",),
            remedies=("기존 명령이 끝난 뒤 다음 명령을 요청하십시오.",),
            evidence_gaps=(),
            specificity=115,
        )

    if "failed to retrieve pid gain values" in lowered:
        return _match(
            rule_id="pid_gain_read_failure",
            family="joint_operation_failure",
            title="PID gain 조회 실패",
            meaning="표시된 관절의 PID gain 값을 하드웨어에서 읽지 못했습니다.",
            confidence="medium",
            causes=("해당 관절의 통신 또는 Ready 상태가 불안정했을 가능성이 있습니다.",),
            checks=("표시된 관절의 CAN 통신, Power/Ready 상태와 인접 timeout 로그를 확인하십시오.",),
            remedies=("관절 통신 상태를 복구한 뒤 PID gain 조회를 다시 수행하십시오.",),
            evidence_gaps=("해당 시각의 CAN 수신 상태와 관절 상태값이 필요합니다.",),
            specificity=105,
        )

    if "destruction error: unable to determine how the control ended" in lowered:
        return _match(
            rule_id="control_destruction_result_unknown",
            family="control_lifecycle_error",
            title="제어 종료 결과 확인 실패",
            meaning="Control 객체 정리 시점에 제어가 종료된 이유를 확인하지 못했습니다.",
            confidence="medium",
            causes=("제어 종료 응답이 누락되었거나 취소·Fault 처리와 동시에 객체가 정리되었을 가능성이 있습니다.",),
            checks=("직전 Control 취소, timeout, Fault 및 Control Manager 상태 변화를 확인하십시오.",),
            remedies=("종료 완료를 확인한 뒤 Control 객체를 정리하고 중복 취소 요청을 피하십시오.",),
            evidence_gaps=("직전 command ID와 제어 종료 응답이 필요합니다.",),
            group_window=5.0,
            specificity=100,
        )

    if lowered.strip() == "result: failed":
        return _match(
            rule_id="service_operation_failed",
            family="service_operation_failure",
            title="서비스 요청 실패",
            meaning="Power, Joint 또는 Tool Flange 서비스 요청이 실패 결과를 반환했습니다.",
            confidence="low",
            confidence_reason="실패 결과는 확인되지만 이 행에는 구체적인 실패 원인이 없습니다.",
            causes=("요청 당시 하드웨어 상태 또는 선행 조건이 충족되지 않았을 가능성이 있습니다.",),
            checks=("같은 component의 직전 Requested 행과 같은 시각의 하드웨어 오류를 확인하십시오.",),
            remedies=("선행 조건과 대상 상태를 확인한 뒤 요청을 다시 수행하십시오.",),
            evidence_gaps=("직전 요청 payload와 세부 하드웨어 오류가 필요합니다.",),
            group_window=5.0,
            specificity=90,
        )

    if "invalid request" in lowered or "are in invalid state" in lowered or "is in invalid state" in lowered:
        return _match(
            rule_id="invalid_control_request",
            family="invalid_control_request",
            title="현재 상태와 맞지 않는 제어 요청",
            meaning="현재 관절 또는 Control Manager 상태에서 수행할 수 없는 명령이 요청되었습니다.",
            confidence="high",
            causes=("Servo/Ready/Control 상태와 요청한 제어 모드가 맞지 않았을 가능성이 있습니다.",),
            checks=("표시된 관절 상태와 직전 명령, Servo On 및 Control Manager 상태를 확인하십시오.",),
            remedies=("필요 상태를 먼저 구성하거나 유효한 제어 순서로 명령을 다시 수행하십시오.",),
            evidence_gaps=("직전 명령 payload와 상태 전환 순서가 있으면 충돌 지점을 확인할 수 있습니다.",),
            specificity=105,
        )

    if "failed to start control cancelling process" in lowered or ("control" in lowered and "cancel" in lowered and "fail" in lowered):
        return _match(
            rule_id="control_cancellation_failure",
            family="control_cancellation_failure",
            title="제어 취소 프로세스 시작 실패",
            meaning="진행 중인 제어를 취소하기 위한 프로세스를 정상적으로 시작하지 못했습니다.",
            confidence="medium",
            causes=("기존 제어 상태가 이미 변경되었거나 취소 처리 스레드가 응답하지 않았을 가능성이 있습니다.",),
            checks=("직전 Control Manager 상태, 활성 command 및 timeout 로그를 확인하십시오.",),
            remedies=("중복 취소 요청을 피하고 현재 제어 종료를 확인한 뒤 다시 시도하십시오.",),
            evidence_gaps=("취소 요청 직전의 제어 상태와 command ID가 필요합니다.",),
            group_window=5.0,
            specificity=100,
        )

    if "failed to schedule" in lowered and "realtime loop" in lowered:
        return _match(
            rule_id="realtime_loop_scheduling_failure",
            family="realtime_scheduling_delay",
            title="RPC 실시간 루프 스케줄링 지연",
            meaning="2 ms 주기의 실시간 하드웨어 또는 제어 루프가 목표 주기에 맞춰 실행되지 못했습니다.",
            severity="warning",
            confidence="medium",
            confidence_reason="스케줄링 실패는 확인되지만 CPU 과열·부하·커널 지연 중 원인 확인이 필요합니다.",
            causes=("RPC CPU 과열, 높은 부하 또는 커널 스케줄링 지연이 발생했을 가능성이 있습니다.",),
            checks=(
                "x86_pkg_temp, CPU load, intel_powerclamp 및 perf 지연 로그를 확인하십시오.",
                "Hardware와 ControlManager 양쪽에서 반복되는지와 발생 주기를 확인하십시오.",
            ),
            remedies=("RPC 방열과 통풍을 개선하고 불필요한 프로세스 부하를 줄이십시오.",),
            evidence_gaps=("같은 시각의 온도, CPU 사용률 및 커널 로그가 필요합니다.",),
            group_window=90.0,
            specificity=110,
        )

    if "tracking error" in lowered or "tracking error limit" in lowered:
        return _match(
            rule_id="joint_tracking_error",
            family="tracking_error",
            title="관절 Tracking Error",
            meaning="현재 관절 상태가 목표 reference를 허용 오차 안에서 추종하지 못했습니다.",
            confidence="medium",
            causes=("과도한 부하, 기구 걸림, 급격한 목표값 또는 gain 설정이 원인일 가능성이 있습니다.",),
            checks=("해당 축의 target/current position, velocity, current와 기구 간섭을 확인하십시오.",),
            remedies=("목표 변화율과 gain을 낮추고 부하 또는 간섭을 제거한 뒤 재시도하십시오.",),
            evidence_gaps=("Fault CSV의 목표/현재값 차이와 전류가 필요합니다.",),
            group_window=1.0,
            specificity=100,
        )

    if "timeout: power command failed" in lowered:
        return _match(
            rule_id="power_command_timeout",
            family="power_command_timeout",
            title="전원 명령 시간 초과",
            meaning="요청한 전원 상태가 제한 시간 안에 확인되지 않았습니다.",
            confidence="medium",
            causes=("PDU 통신, 안전 입력 또는 실제 전원 출력 상태가 명령과 일치하지 않았을 가능성이 있습니다.",),
            checks=("대상 전원 레일, PDU 상태 응답, EMO/SCB 입력 및 CAN 통신을 확인하십시오.",),
            remedies=("안전 입력과 PDU 통신을 정상화한 뒤 전원 명령을 다시 수행하십시오.",),
            evidence_gaps=("대상 레일과 PDU 응답 프레임이 필요합니다.",),
            specificity=95,
        )

    if "timeout" in lowered and severity in {"error", "critical"}:
        return _match(
            rule_id="generic_timeout",
            family="timeout",
            title="처리 시간 초과",
            meaning="요청 또는 상태 갱신이 제한 시간 안에 완료되지 않았습니다.",
            confidence="low",
            confidence_reason="Timeout은 확인되지만 대상과 선행 원인이 충분히 구조화되지 않았습니다.",
            causes=("통신 지연, 상대 장치 무응답 또는 처리 부하가 원인일 가능성이 있습니다.",),
            checks=("오류의 component, 직전 명령 및 같은 시각의 상태 전환을 확인하십시오.",),
            remedies=("대상 통신과 상태를 확인한 뒤 명령을 재시도하십시오.",),
            evidence_gaps=("대상 장치, 요청 ID 및 인접 로그가 필요합니다.",),
            group_window=5.0,
            specificity=60,
        )

    if severity in {"error", "critical"} or category in {"failure", "timeout"} or result in {
        "failed",
        "failure",
        "timeout",
    }:
        derived_severity = "critical" if severity == "critical" else "error"
        return _match(
            rule_id="unknown_error",
            family="unknown_error",
            title="미분류 오류",
            meaning="오류 또는 실패가 기록되었지만 현재 진단 규칙으로 세부 유형을 확정하지 못했습니다.",
            severity=derived_severity,
            confidence="low",
            confidence_reason="원본 오류는 확인되지만 알려진 원인 패턴과 충분히 일치하지 않습니다.",
            causes=("현재 로그만으로 직접 원인을 특정할 수 없습니다.",),
            checks=("같은 시각 전후 3초의 명령, 상태 전환, Fault 및 전원 로그를 확인하십시오.",),
            remedies=("원본 근거와 재현 조건을 확보한 뒤 관련 구성요소부터 단계적으로 분리 점검하십시오.",),
            evidence_gaps=("재현 절차, 직전 명령, 영향 범위 및 Fault CSV가 필요합니다.",),
            group_window=0.5,
            specificity=10,
        )
    return None
