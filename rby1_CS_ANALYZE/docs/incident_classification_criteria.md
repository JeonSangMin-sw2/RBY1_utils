# RB-Y1 CS 분석기 에러/장애 분류 기준 가이드

본 문서는 **RB-Y1 CS 로그 분석기(V5)**에서 로봇 시스템의 로그와 Fault CSV를 분석할 때 **문제를 어떤 기준으로 분류하고, 어떤 키워드와 명령을 매칭하며, 심각도(고심각도 / 오류 / 주의)를 어떻게 판정하는지**를 체계적으로 정리한 기준서입니다.

진단 규칙은 [`config/error_guide.yaml`](file:///home/rainbow/utils_ws/rby1_CS_ANALYZE/config/error_guide.yaml) 및 [`config/command_dictionary.yaml`](file:///home/rainbow/utils_ws/rby1_CS_ANALYZE/config/command_dictionary.yaml)에 정의되어 있으며, 필요 시 언제든지 수정/확장할 수 있습니다.

---

## 1. 심각도(Severity) 판정 기준

| 심각도 수준 | UI 뱃지 | 판정 기준 및 시스템 영향도 |
| :--- | :---: | :--- |
| **고심각도 (Critical)** | 🛑 `고심각도 오류` | • **로봇의 즉시 정지 또는 FSM Major/Minor Fault를 유발**하는 치명적 장애<br>• EMO 비상정지 스위치 눌림, 48V 파워 강제 차단/불일치, 모터 엔코더/드라이브 결함, 배터리 급강하로 인한 PC 셧다운, 실시간 루프 심각 지연 |
| **오류 (Error)** | ❌ `오류` | • 특정 관절/서비스 명령이 거부되거나 타임아웃되어 동작을 수행하지 못한 상태<br>• 통신 타임아웃(배선/보드), Zero Position 유실, 퓨즈 단선, 부가장치(그리퍼/리더암) udev 미인식 등 |
| **주의 (Warning)** | ⚠️ `주의` | • 즉각적인 정지는 아니지만 브레이크 밀림, 솔레노이드 축 틀어짐, 주기적 통신 지연, 갱신 지연 등 잠재적 기구/통신 리스크가 감지된 상태 |
| **정보/명령 (Info)** | 📡/⚡ `UPC/RPC` | • 상위 PC(UPC)의 제어 송신 명령 또는 내부 제어 서비스(RPC)의 정상 처리 완료 응답 |

---

## 2. 대분류(Major Category) 체계

1. **모터 / 조인트 (`motor_joint`)**: 서보온 실패, Arm 6축/손목 기동 이상, 모터 통신 미수신, 드라이브 비트 에러, 추종 오차, 가동범위 초과 모터 과열, 브레이크 기구 이상, 원점(Zero Position) 유실
2. **Control Manager (`control_manager`)**: FSM 상태 전환, Major/Minor Fault 진입, 제어 라이프사이클 종료, 실시간 루프 지연
3. **하드웨어 / 전원 (`hardware_power`)**: 48V/24V/12V/5V 전원 레일 불일치, EMO 비상정지 스위치, 파트별 퓨즈 단선, 배터리 전압 급강하 및 PC 셧다운
4. **CAN / 네트워크 통신 (`communication`)**: CAN 버스 타임아웃, 관절 상태 갱신 주기 지연, LAN 모듈 통신
5. **안전 / 기구학 (`safety_kinematics`)**: 소프트웨어/하드웨어 위치 한계(Position Limit), Cartesian 특이점(Singularity)
6. **서비스 / RPC / 부가장치 (`service_api`)**: gRPC 명령 타임아웃, 제어 취소 실패, 그리퍼/툴플렌지 연동, 리더암 연동, 헤드 펌웨어 방향, 미분류 오류

---

## 3. 세부 문제 분류 규칙 및 확인 키워드 매핑 테이블

### [하드웨어 / 전원 (hardware_power)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 단계별 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `emo_switch_error` | **EMO 스위치 활성** | `emo error.*check emo switch` | `Hardware::ExecutePowerCommand` | 🛑 `critical` | `root` | • **원인**: 본체 비상정지 눌림 또는 무선 리모컨 E-STOP 작동/페어링 불량<br>• **조치**: EMO 버튼 해제 및 리모컨-리시버 페어링 점검 |
| `power_48v_state_unmatched_timeout` | **48V 전원 명령 상태 불일치** | `power command failed.*unmatched power states.*48v` | `Hardware::ExecutePowerCommand` | ❌ `error` | `root` | • **원인**: EMO 활성 상태에서 48V 투입 시도, SCB 리셋 신호 부재, FORT 방전, PDU 퓨즈 단선<br>• **조치**: EMO 해제 후 48V 재인가, 리모컨 배터리 점검, SCB Reset 전송 |
| `part_fuse_blown_servo_on_failure` | **파트별 전원 퓨즈 단선 의심** | `(?:left_arm\|right_arm\|torso\|head\|mobile)\s+servo.?on\s+fail` | `PDU`, 각 파트 전원 | ❌ `error` | `root` | • **원인**: PDU 내부 특정 파트 48V 전원 퓨즈 단선<br>• **조치**: PDU를 열어 해당 파트 퓨즈 도통 시험 후 규격 퓨즈로 교체 |
| `battery_shutdown_on_servo_on` | **서보온 시 전원 불안정 / PC 셧다운** | `pc\s*(?:shutdown\|shutting down)\|power drop` | `Battery`, `PDB`, `Torso` | 🛑 `critical` | `root` | • **원인**: 배터리 셀 불량/전압 급강하(54V 미만), Torso UVW 쇼트, 전원 분배 보드 이상<br>• **조치**: Torso UVW 쇼트 체크/저항 측정 → 배터리 전압 점검 및 배터리 교체 |
| `power_state_changed_during_enable` | **Enable 중 전원 상태 변경 감지** | `power state.*changed during enable state` | `Hardware::PowerManager` | 🛑 `critical` | `root` | • **원인**: 서보 온 상태에서 메인 전원 레일 강제 차단 또는 PDU 통신 유실<br>• **조치**: 전원 커넥터 접촉 불량 및 PDU CAN 배선 점검 |

---

### [모터 / 조인트 (motor_joint)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 단계별 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `isolated_arm6_servo_on_failure` | **Arm 6축 / 손목부 Servo On 실패** | `(?:right\|left)_arm_6.*(?:servo.?on\|initializ).*fail` | `arm_6`, `WY2` | ❌ `error` | `root` | • **원인**: EEPROM 휘발로 BNO 15번 리셋, 손목 보드 프리셋 핀 납땜 누락, 코인셀 방전, 모터 고착/타이밍 벨트 마모<br>• **조치**: BNO 15번 확인 시 BNO 재설정 및 보드 교체 / 프리셋 핀 납땜(쇼트) 후 글루건 고정 및 원점 재보정 / 풀리(MXL20/40), 벨트(MXL82) 교체 |
| `wy2_zero_position_loss` | **WY2 관절 원점(Zero Position) 유실** | `zero position (?:lost\|changed\|random)` | `WY2`, 손목 보드 | ❌ `error` | `root` | • **원인**: WY2 손목 보드 프리셋 신호핀 납땜 누락으로 48V 전원 On/Off 시마다 위치값 랜덤 변경<br>• **조치**: 손목 보드 프리셋 신호핀 납땜(쇼트) + 케이블 글루건 고정 + 원점 재보정 |
| `joint_state_update_timeout` | **관절 상태 갱신 시간 초과 (배선/통신)** | `timeout:\s*joint\s*['"]?([^'"]+).*state update exceeded` | 각 관절, `WY1` | ❌ `error` | `root` | • **원인**: WY1(`left_arm_4`) 관절부 조립체 내부 배선 단선/쇼트, BNO 15번 초기화, PDU 퓨즈 단선<br>• **조치**: 멀티미터로 CAN 라인(120Ω) 및 신호선 저항 측정 → 쇼트 확인 시 WY1 조립체 교체 |
| `motor_temp_error` | **모터 TEMP 오류 (과열 및 가동범위 초과)** | `\btemp(?:erature)?\s+error\b` | 각 관절 모터 | ❌ `error` | `root` | • **원인**: 가동범위(Limit) 초과 상태에서 강제 Servo On 시 지속 전류 인가로 과열 및 코일 소손<br>• **조치**: 수동으로 정상 가동 범위 내로 이동 후 냉각, 코일 소손/고착 시 모터 교체 |
| `brake_mechanism_failure` | **브레이크 기구 이상 및 밀림** | `brake (?:slip\|drag\|stuck\|late)\|solenoid (?:fault\|stuck)` | 브레이크 어셈블리 | ⚠️ `warning` | `root` | • **원인**: 브레이크 윙 마모/스프링 탄성 저하, 솔레노이드 축 틀어짐, 아우터 볼트 풀림, 배선 걸림<br>• **조치**: 배선 간섭 제거, 브레이크 아우터 볼트 규격 토크(**28 kgf·cm**) 체결, 윙 스프링 교체 및 원점 재보정 |

---

### [서비스 / RPC / 부가장치 (service_api)]
| 규칙 ID | 장애 제목 | 확인 키워드 / 매칭 패턴 (정규식) | 대상 명령 / 컴포넌트 | 심각도 | 역할 | 핵심 원인 가설 및 단계별 조치 |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| `peripheral_gripper_udev_error` | **그리퍼 UDEV 미인식 또는 통신 이상** | `gripper (?:not found\|open failed)\|rby1_gripper` | 그리퍼, `UPC` | ❌ `error` | `root` | • **원인**: UPC 터미널 내 udev 미설정(`/dev/rby1_gripper` 미생성), WY2 CAN 단선, 백팩 바이패스 연결 불량<br>• **조치**: `ls -l /dev/`로 udev 확인 및 설정, 멀티미터로 바이패스-툴플렌지 간 도통 확인 |
| `leader_arm_connection_error` | **리더암(Leader Arm) 미인식 또는 관절 이상** | `leader.?arm (?:not found\|failed)\|master.?arm` | 리더암, `U2D2` | ❌ `error` | `root` | • **원인**: UPC udev 미설정(`/dev/rby1_leader_arm`), U2D2 케이블 단선, 조인트 볼트 누락/조립자세 틀림<br>• **조치**: udev 설정 확인, U2D2 LED 및 케이블 점검, 조립 상태 확인 및 재체결 |
| `head_firmware_reverse_direction` | **헤드 펌웨어 역방향 제어 오류** | `head direction (?:reversed\|opposite)` | `Head`, 모터 제어기 | ❌ `error` | `root` | • **원인**: 헤드 모터 제어 보드에 역방향 펌웨어가 플래싱됨<br>• **조치**: 펌웨어 부트로더 프로그램을 사용하여 정방향 펌웨어로 재플래싱 |

---

---

## 4. 다차원 상태 플래그(Diagnostic Flags) 및 복합 랭킹 추론 시스템

RB-Y1 CS 분석기는 단일 룰 매칭의 한계를 극복하기 위해 **10종 핵심 상태 플래그(Flags)**를 실시간 추출하고, **2개 이상의 플래그가 동시 발생한 복합 이상 조건(Combinations)**을 최우선 순위(Rank 1)로 판정합니다.

### 4.1 핵심 상태 플래그 10종 정의

| 플래그 ID | 뱃지 라벨 | 아이콘 | 주요 감지 키워드 | 시스템 상태 의미 |
| :--- | :--- | :---: | :--- | :--- |
| `is_timeout` | **타임아웃 발생** | ⏱️ | `timeout`, `timed out`, `deadline exceeded` | 통신 응답 지연 또는 제어 루프 주기 시간 초과 |
| `is_major_fault` | **Major Fault 발생** | 🛑 | `major fault`, `majorfault`, `fsm state` | 시스템 안전 정지 및 FSM Major Fault 전환 |
| `is_minor_fault` | **Minor Fault 발생** | ⚠️ | `minor fault`, `minorfault` | 모터 드라이버 경고 또는 일시적 제어 거절 |
| `is_command_canceled` | **제어 명령 취소/거절** | ⚡ | `canceled`, `preempted`, `command rejected` | 상위 제어기(UPC)의 제어권 선점 또는 명령 취소 |
| `is_emo_triggered` | **EMO 비상정지 작동** | 🚨 | `emo error`, `e-stop`, `emergency stop` | 비상정지 스위치 눌림 또는 무선 리모컨 E-STOP |
| `is_power_loss` | **전원 이상/차단** | 🔌 | `power off`, `power drop`, `unmatched power`, `fuse` | 48V/24V 전원 레일 불일치, 전압 급강하, 퓨즈 단선 |
| `is_motor_temp_high` | **모터 과열/온도 오류** | 🌡️ | `temp error`, `overheat` | 기구적 한계 상태에서의 지속 전류 인가로 코일 과열 |
| `is_tracking_error` | **관절 추종 오차** | 📈 | `tracking error`, `following error` | 외력 충돌 또는 과부하로 인한 궤적 추종 실패 |
| `is_comm_lost` | **모터 통신 두절** | 📡 | `no data received`, `comm lost`, `can timeout` | 관절 하네스 배선 단선/쇼트, BNO 15번 리셋 |
| `is_zero_pos_lost` | **원점(Zero Pos) 유실** | 🎯 | `zero position lost`, `preset loss` | 손목 보드 프리셋 핀 누락으로 전원 리셋 시 원점 유실 |

### 4.2 복합 플래그 조합 (Combinations) 및 랭킹 규칙

사건(Incident) 발생 시 다음과 같은 우선순위로 원인 가설 및 대응 방안이 나열됩니다:

1. **Rank 1 (최우선)**: **복합 플래그 조건 (2개 이상 동시 만족)**
   - `is_major_fault` + `is_timeout`: 통신 두절에 따른 실시간 제어 루프 중단 및 Major Fault 전환 종합 조치
   - `is_major_fault` + `is_emo_triggered`: EMO 비상정지 작동으로 인한 48V 주전원 차단 및 셧다운 복구
   - `is_major_fault` + `is_power_loss`: 모터 구동 중 48V 급강하 / PDU 퓨즈 단선에 따른 안전 정지 조치
   - `is_timeout` + `is_comm_lost`: 조인트 내부 하네스 배선 단선에 따른 CAN 통신 완전 두절 점검
   - `is_command_canceled` + `is_minor_fault`: 관절 가동 범위 초과 및 FSM 충돌로 인한 제어 명령 취소
   - `is_motor_temp_high` + `is_tracking_error`: 물리적 간섭/부하로 인한 모터 과열 및 추종 오차
2. **Rank 2**: **도메인 특화 특정 룰 (특화 부품 매칭 원인 및 조치)**
   - Arm 6축 손목 BNO 리셋, WY2 원점 유실, Torso UVW 단락 등
3. **Rank 3**: **감지된 개별 플래그별 세부 점검 및 대응 방안**
   - 활성화된 각 플래그의 기본 점검 항목 및 예방 조치 가이드 순차 제공

---

## 5. 진단 규칙(`error_guide.yaml`) 수정 및 신규 추가 방법

새로운 에러 로그 패턴이나 플래그, 복합 조합을 추가하려면 [`config/error_guide.yaml`](file:///home/jsm/RBY1_utils/rby1_CS_ANALYZE/config/error_guide.yaml) 파일에 항목을 추가하면 즉시 반영됩니다.
