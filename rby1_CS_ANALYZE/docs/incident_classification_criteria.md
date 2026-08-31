# RB-Y1 CS 분석기 에러/장애 분류 기준 가이드

본 문서는 **RB-Y1 CS 로그 분석기(V5)**에서 로봇 시스템의 로그와 Fault CSV를 분석할 때 **문제를 어떤 기준으로 분류하고, 어떤 키워드와 명령을 매칭하며, 심각도(고심각도 / 오류 / 주의)를 어떻게 판정하는지**를 체계적으로 정리한 기준서입니다.

진단 규칙은 [`config/error_guide.yaml`](file:///home/jsm/RBY1_utils/rby1_CS_ANALYZE/config/error_guide.yaml) 및 [`config/command_dictionary.yaml`](file:///home/jsm/RBY1_utils/rby1_CS_ANALYZE/config/command_dictionary.yaml)에 정의되어 있으며, 필요 시 언제든지 수정/확장할 수 있습니다.

---

## 1. 심각도(Severity) 판정 기준

| 심각도 수준 | UI 뱃지 | 판정 기준 및 시스템 영향도 |
| :--- | :---: | :--- |
| **고심각도 (Critical)** | 🛑 `고심각도 오류` | • **로봇의 즉시 정지 또는 FSM Major/Minor Fault를 유발**하는 치명적 장애<br>• EMO 비상정지 스위치 눌림, 48V 파워 강제 차단/불일치, 모터 엔코더/드라이브 결함, 실시간 루프 심각 지연 |
| **오류 (Error)** | ❌ `오류` | • 특정 관절/서비스 명령이 거부되거나 타임아웃되어 동작을 수행하지 못한 상태<br>• 제어 취소 실패, 파라미터 불일치, 서비스 연산 실패, 관절 한계 도달 등 |
| **주의 (Warning)** | ⚠️ `주의` | • 즉각적인 정지는 아니지만 주기적 통신 지연, 갱신 지연, 잠재적 리스크가 감지된 상태 |
| **정보/명령 (Info)** | 📡/⚡ `UPC/RPC` | • 상위 PC(UPC)의 제어 송신 명령 또는 내부 제어 서비스(RPC)의 정상 처리 완료 응답 |

---

## 2. 대분류(Major Category) 체계

1. **모터 / 조인트 (`motor_joint`)**: 서보온 실패, Arm 6축 기동 이상, 모터 통신 미수신, 드라이브 비트 에러, 추종 오차
2. **Control Manager (`control_manager`)**: FSM 상태 전환, Major/Minor Fault 진입, 제어 라이프사이클 종료
3. **하드웨어 / 전원 (`hardware_power`)**: 48V/24V/12V/5V 전원 레일 불일치, EMO 비상정지 스위치, 배터리 저전압
4. **CAN / 네트워크 통신 (`communication`)**: CAN 버스 타임아웃, 관절 상태 갱신 주기 지연
5. **안전 / 기구학 (`safety_kinematics`)**: 소프트웨어/하드웨어 위치 한계(Position Limit), Cartesian 특이점(Singularity)
6. **서비스 / RPC (`service_api`)**: gRPC 명령 타임아웃, 제어 취소 실패, 우선순위 부족 거부, 미분류 오류

---

## 3. 세부 문제 분류 규칙 및 확인 키워드 매핑 테이블

### [하드웨어 / 전원 (hardware_power)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `emo_switch_pressed` | **EMO 스위치 활성** | `EMO is pressed` | `Hardware::ExecutePowerCommand` | 🛑 `critical` | `root` | • **원인**: 비상정지 스위치가 눌려 있거나 SCB/FORT 비상정지 신호 단선<br>• **조치**: EMO 버튼 해제 및 리모컨 비상정지 해제 확인 |
| `power_48v_mismatch` | **48V 전원 명령 상태 불일치** | `unmatched power states: 48` | `Hardware::ExecutePowerCommand` | ❌ `error` | `root` | • **원인**: EMO 활성 상태에서 48V 투입 시도, SCB 리셋 신호 부재, FORT 방전<br>• **조치**: EMO 해제 후 48V 재인가, 리모컨 배터리 점검 |
| `power_changed_while_enabled` | **Enable 중 전원 상태 변경 감지** | `Power status changed while enabled` | `Hardware::PowerManager` | 🛑 `critical` | `root` | • **원인**: 서보 온 상태에서 48V 메인 전원 레일 강제 차단<br>• **조치**: 전원 커넥터 접촉 불량 점검 |
| `power_command_timeout` | **전원 제어 명령 타임아웃** | `Timeout: Power command failed` | `Hardware::ExecutePowerCommand` | ❌ `error` | `root` | • **원인**: SCB 보드 응답 지연 또는 CAN 통신 끊김<br>• **조치**: SCB 보드 전원 및 CAN 배선 점검 |

---

### [모터 / 조인트 (motor_joint)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `arm6_wakeup_fail` | **Arm 6축 Servo On 실패** | `wakeup.*(right_arm_6\|left_arm_6)` | `right_arm_6`, `left_arm_6` | 🛑 `critical` | `root` | • **원인**: 손목 말단 모터 48V 전원 인가 지연, CAN 통신 패킷 유실<br>• **조치**: 48V 전원 투입 후 1.5초 대기 후 서보온, 케이블 꺾임 점검 |
| `motor_no_response` | **모터 통신 미수신** | `no response from motor\|communication lost` | `JointManager`, 각 관절 | 🛑 `critical` | `root` | • **원인**: 드라이브 전원 단절 또는 CAN 버스 노이즈/단선<br>• **조치**: 해당 축 하네스 결착 상태 및 종단 저항(120Ω) 점검 |
| `drive_bit_error` | **모터 드라이브 비트 에러** | `drive bit error\|drive error bit` | 각 관절 모터 | 🛑 `critical` | `root` | • **원인**: 드라이버 과열, 과전류, 엔코더 이상 카운트<br>• **조치**: 관절 기구 걸림 여부 및 모터 온도 확인 후 드라이브 리셋 |
| `tracking_error_exceeded` | **관절 추종 오차 한계 초과** | `tracking error exceeded\|position error limit` | 각 관절 | ❌ `error` | `root` | • **원인**: 과도한 부하, 외력 충돌, 게인 튜닝 부족<br>• **조치**: 모션 프로파일 가감속 완화 및 로봇 충돌 여부 확인 |

---

### [Control Manager & Fault (control_manager)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `major_fault_reaction` | **MajorFault 대응 절차 시작** | `major fault reaction started` | `ControlManager` | 🛑 `critical` | `reaction` | • **원인**: 선행된 치명적 오류에 의해 시스템이 안전 정지 모드로 진입<br>• **조치**: 같은 시각 직전의 '최초 원인(Root)' 노드를 확인 후 복구 |
| `minor_fault_reaction` | **MinorFault 대응 절차 시작** | `minor fault reaction started` | `ControlManager` | ❌ `error` | `reaction` | • **원인**: 경미한 제어 이상으로 인한 안전 모드 전환<br>• **조치**: 이상 유발 관절 점검 후 MinorFault Reset 수행 |
| `state_transition_failure` | **Control Manager 상태 전이 거부** | `cannot transit from .* to` | `ControlManager` | ❌ `error` | `root` | • **원인**: 현재 FSM 상태에서 허용되지 않는 명령 요청<br>• **조치**: 현재 상태 머신 상태(`MajorFault` 여부 등) 확인 후 절차적 전이 |
| `realtime_loop_overrun` | **실시간 제어 루프 지연 (Overrun)** | `control loop overrun\|loop deadline missed` | `ControlLoop` | ⚠️ `warning` | `warning` | • **원인**: CPU 부하 과중, 비실시간 스레드 간섭, 로깅 지연<br>• **조치**: CPU 격리(isolcpus) 및 실시간 우선순위(SCHED_FIFO) 확인 |

---

### [안전 및 기구학 (safety_kinematics)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `joint_position_limit` | **관절 위치 한계 초과** | `joint limit reached\|position limit violated` | 각 관절 | ❌ `error` | `root` | • **원인**: 가동 범위를 벗어난 목표 궤적 전달<br>• **조치**: 모션 플래너의 Joint Limit 범위 파라미터 점검 |
| `singularity_detected` | **Cartesian 특이점 근접** | `singularity detected\|near singularity` | `KinematicsManager` | ⚠️ `warning` | `warning` | • **원인**: 팔 관절이 완전히 펴지거나 축이 정렬되는 특이점 영역 진입<br>• **조치**: 궤적 경로에 중간 경유점(Waypoint) 추가 |

---

### [서비스 및 RPC (service_api)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `cancel_control_failed` | **제어 취소 프로세스 시작 실패** | `CancelControl.*failed\|failed to start cancel` | `ControlManager::CancelControl` | ❌ `error` | `root` | • **원인**: 이미 제어 상태가 변경되었거나 취소 처리 스레드가 응답하지 않음<br>• **조치**: ControlManager FSM 상태 확인 및 프로세스 재기동 |
| `grpc_priority_rejected` | **우선순위 부족으로 명령 거부** | `priority too low\|request rejected by priority` | `ServiceAPI` | ❌ `error` | `root` | • **원인**: 현재 실행 중인 고우선순위 제어권 점유 상태<br>• **조치**: 이전 작업 완료 또는 우선순위 레벨 상향 요청 |
| `unclassified_error` | **미분류 오류** | `error\|failed\|exception` (기타 미매칭) | 일반 서비스 | ❌ `error` | `root` | • **원인**: 정의되지 않은 런타임 오류<br>• **조치**: 원본 로그 전문(Raw Log)을 확인하여 신규 규칙 등록 |

---

## 4. 진단 규칙(`error_guide.yaml`) 수정 및 신규 추가 방법

새로운 에러 로그 패턴을 분석기에 추가하거나 기존 진단 설명을 수정하려면 [`config/error_guide.yaml`](file:///home/jsm/RBY1_utils/rby1_CS_ANALYZE/config/error_guide.yaml) 파일의 `rules` 목록에 아래 형식으로 항목을 추가하면 즉시 반영됩니다:

```yaml
- id: my_custom_error_id               # 고유 룰 ID
  major_category: motor_joint          # 대분류 (motor_joint, control_manager, hardware_power, communication, safety_kinematics, service_api)
  sub_category: tracking_error         # 소분류
  family: tracking_error               # 패밀리 그룹
  title: "관절 토크 과부하 감지"       # UI에 표시될 장애 제목
  pattern: "torque overload detected"  # 로그에서 감지할 키워드 또는 정규식
  meaning: "관절에 정격 이상의 토크가 지속 인가되었습니다." # 발생 장애 개요
  severity: critical                   # 심각도 (critical / error / warning)
  role: root                           # 역할 (root: 원인, reaction: 반응, warning: 주의)
  confidence: high                     # 진단 신뢰도 (high / medium / low)
  causes:                              # 가능한 원인 후보 목록
    - "외력 충돌 또는 기구적 걸림"
    - "과도한 가반하중 운반"
  checks:                              # 현장 엔지니어 점검 항목 (STEP 1)
    - "해당 관절 주변의 물리적 간섭 및 물체 충돌 여부를 육안 점검하십시오."
    - "적재 하중이 로봇 사양(Payload) 이내인지 확인하십시오."
  remedies:                            # 복구 조치 방안 (STEP 2)
    - "간섭 물체를 제거하고 모터를 재부팅하십시오."
  specificity: 80                      # 우선순위 가중치 (높을수록 우선 매칭)
```
