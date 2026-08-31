# 제작자
- v1~4 : 권기성
- v5 : 전상민

# RB-Y1 CS 로그 분석기 V5

RB-Y1 RPC 로그, Fault CSV, ZIP/TAR/GZIP 묶음을 로컬에서 분석하는 CS 장애 분석 도구입니다.
로그의 개별 오류를 장애 사건 단위로 묶고, 발생 시각, 최초 오류, 상태 전환, 후속 반응, 가능한 원인,
확인 항목과 대응 방법을 한 화면에 보여줍니다.

## 주요 기능

- **로그**: 명령 실패, 오류, Minor/Major Fault를 사건 단위로 구성하고 대표 중대 사건을 강조합니다.
- **CSV**: 위치, 속도, 전류, 토크, 전원 및 제어 상태와 모터 상태 비트를 그래프로 확인합니다.
- **시각화**: Fault CSV의 joint position을 시간축과 동기화하여 RB-Y1 3D 자세로 재생합니다.
- **다이나믹스 분석**: 순기구학(FK) 4x4 동차 변환 행렬, 관절별 부하율(%), 이론 토크 대비 실측 토크 및 외란/잔차 토크 이상 감지를 지원합니다.
- A Type V1.0~V1.2와 M Type V1.0~V1.3 모델을 지원하며, 로그에서 모델을 확인할 수 없으면 wheel 신호로 타입을 추론합니다.
- 버전까지 확인할 수 없으면 정밀구동 헤드가 적용된 V1.2를 사용합니다.
- 조인트 전체/일부/해제 선택, 제로 자세, 재생 속도, 카메라 회전·이동·확대/축소를 지원합니다.
- LOG/CSV/ZIP/TAR/TAR.GZ/GZIP과 파일이 담긴 폴더의 재귀 드래그앤드롭을 지원합니다.

위치와 속도는 각각 `deg`, `deg/s`, 전류와 토크는 `A`, `Nm` 단위로 표시합니다. 모든 분석은
로컬에서 수행되며 서버는 `127.0.0.1`에만 바인딩됩니다. 텔레메트리나 외부 런타임 네트워크
의존성은 없습니다.

## 폴더 구조

```text
rby1_CS_ANALYZE/
├── config/                      # 오류 가이드 및 규칙 설정 (error_guide.yaml)
├── data/                        # 통합 데이터셋 및 분석 케이스 저장소 (cases/)
├── frontend/                    # React / Vite 프론트엔드 UI
├── backend/                     # Python 장애 분석 백엔드 패키지
├── packaging/                   # PyInstaller spec 및 엔트리포인트
├── build.sh                     # V5 Onefile 자동 빌드 & 배포 스크립트
└── main.py                      # 루트 메인 실행 파일
```

## 환경 구성 및 실행

### 1. 사전 요구 사항 (Prerequisites)

- **Python**: `3.10` 이상
- **Node.js**: `20.x` 이상 (LTS 권장)

### 2. 가상환경 및 의존성 설치

```bash
# Python 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 백엔드/테스트/패키징 의존성 설치
pip install -e '.[test,package]'

# 프론트엔드 의존성 설치 및 UI 빌드
npm --prefix frontend ci
npm --prefix frontend run build
```

### 3. 로컬 실행

```bash
# 루트 실행 파일로 실행 (권장: Chrome/Chromium 앱 모드로 자동 실행)
python main.py

# 브라우저 자동 실행 없이 URL만 출력할 경우
python main.py --no-open-browser

# 또는 모듈 직접 실행 시 (backend 경로 지정)
PYTHONPATH=backend python -m rby1_analyzer.launcher
```

---

## 배포 패키지 빌드 (One-file 전용)

PyInstaller를 사용하여 별도의 Python/Node.js 환경이 없는 대상 PC에서도 바로 실행 가능한 **단일 독립 실행 파일 (One-file 바이너리)** 을 빌드합니다.

### 🚀 원클릭 빌드 & 배포 패키징 (가장 간편한 방법)
아래 스크립트를 실행하면 **UI 빌드 + Onefile 바이너리 빌드(`dist/rby1-cs-analyzer-v5`) + 실행 권한(0755) 부여 + tar.gz 압축**까지 한 번에 완료됩니다:
```bash
./build.sh
```
- **배포 산출물**: `dist/rby1-cs-analyzer-v5-linux-x86_64.tar.gz` 및 `dist/rby1-cs-analyzer-v5`
- **배포 방법**: 생성된 단일 실행 파일 `rby1-cs-analyzer-v5` 또는 `.tar.gz` 파일 1개만 다른 PC로 전달하여 실행합니다.

---

### 💾 데이터 저장 위치 및 자동 생성 안내

분석 결과 및 업로드된 로그/케이스 데이터는 **프로그램 실행 시 자동으로 생성**되므로, 별도로 `data` 폴더를 복사하거나 만들어 둘 필요가 없습니다.

- **독립 실행 바이너리 실행 시 기본 경로**:
  - **Linux**: `~/.local/share/rby1-cs-analyzer-v5/cases/`
  - **Windows**: `%LOCALAPPDATA%\RB-Y1 CS Analyzer V5\cases\`
- **소스코드/개발 환경 실행 시**: 프로젝트 루트의 `./data/cases/` (존재할 경우)
- **저장 위치 직접 지정**: `--data-root <경로>` 옵션으로 원하는 디렉터리를 지정할 수 있습니다. (지정한 경로도 자동 생성됨)

---

## 검증

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
./build.sh
```
