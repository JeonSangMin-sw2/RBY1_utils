# RB-Y1 CS 로그 분석기 V4

RB-Y1 RPC 로그, Fault CSV, ZIP/TAR/GZIP 묶음을 로컬에서 분석하는 CS 장애 분석 도구입니다.
로그의 개별 오류를 장애 사건 단위로 묶고, 발생 시각, 최초 오류, 상태 전환, 후속 반응, 가능한 원인,
확인 항목과 대응 방법을 한 화면에 보여줍니다.

## 주요 기능

- **로그**: 명령 실패, 오류, Minor/Major Fault를 사건 단위로 구성하고 대표 중대 사건을 강조합니다.
- **CSV**: 위치, 속도, 전류, 토크, 전원 및 제어 상태와 모터 상태 비트를 그래프로 확인합니다.
- **시각화**: Fault CSV의 joint position을 시간축과 동기화하여 RB-Y1 3D 자세로 재생합니다.
- A Type V1.0~V1.2와 M Type V1.0~V1.3 모델을 지원하며, 로그에서 모델을 확인할 수 없으면 wheel 신호로 타입을 추론합니다.
- 버전까지 확인할 수 없으면 정밀구동 헤드가 적용된 V1.2를 사용합니다.
- 조인트 전체/일부/해제 선택, 제로 자세, 재생 속도, 카메라 회전·이동·확대/축소를 지원합니다.
- LOG/CSV/ZIP/TAR/TAR.GZ/GZIP과 파일이 담긴 폴더의 재귀 드래그앤드롭을 지원합니다.
- 대용량 폴더 및 압축파일은 업로드 단계부터 진행률을 표시합니다.

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
└── main.py                      # 루트 메인 실행 파일
```

## 환경 구성 및 실행

### 1. 사전 요구 사항 (Prerequisites)

- **Python**: `3.10` 이상
- **Node.js**: `20.x` 이상 (LTS 권장)
  > [!NOTE]
  > Ubuntu 기본 저장소의 Node.js는 구버전일 수 있으므로 **NVM(Node Version Manager)**을 통한 설치를 권장합니다.

```bash
# NVM 설치 및 Node.js 20 설정
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 20
nvm use 20
node -v   # v20.x.x 확인
```

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

## 배포 패키지 빌드 (로컬 빌드)

PyInstaller를 사용하여 별도의 Python/Node.js 환경이 없는 대상 PC에서도 바로 실행 가능한 바이너리 패키지를 빌드할 수 있습니다.

> [!IMPORTANT]
> 바이너리 빌드 전 반드시 프론트엔드 빌드가 완료되어 `frontend/dist`가 생성되어 있어야 합니다 (`npm --prefix frontend run build`).

### 📦 빌드 단계 (Build Steps)

#### 🚀 원클릭 빌드 & 배포 패키징 (가장 간편한 방법)
아래 스크립트를 실행하면 **UI 빌드 + Onefile 바이너리 빌드 + 실행 권한(0755) 부여 + tar.gz 압축**까지 한 번에 완료됩니다:
```bash
./build.sh
```
- **배포 산출물**: `dist-onefile/rby1-cs-analyzer-v4-linux-x86_64.tar.gz`
- **배포 방법**: 해당 `.tar.gz` 파일을 다른 PC로 전달하여 압축을 풀면 `chmod` 없이 바로 실행 가능합니다.

---

#### 🛠️ 단계별 수동 빌드 방법

```bash
# 1. 가상환경 활성화
cd /home/rainbow/utils_ws/rby1_CS_ANALYZE
source .venv/bin/activate

# 2. 프론트엔드 UI 빌드 (필수: frontend/dist 생성)
npm --prefix frontend run build

# 3-A. 단일 파일 패키지 빌드 (Onefile - 파일 1개 배포용)
pyinstaller --clean --noconfirm --distpath dist-onefile --workpath build-onefile packaging/rby1-cs-analyzer-v4-onefile.spec

# 3-B. 폴더형 패키지 빌드 (Onedir - 시작 속도 우선)
pyinstaller --clean --noconfirm packaging/rby1-cs-analyzer-v4.spec
```

---

### 1. 단일 파일 패키지 (Onefile - 파일 1개 배포용, camera_ws 방식)

- **생성 위치**: `dist-onefile/rby1-cs-analyzer-v4` (약 35MB)
- **실행**:
  ```bash
  ./dist-onefile/rby1-cs-analyzer-v4
  ```
- **배포 방식**: 생성된 단일 실행 파일 `rby1-cs-analyzer-v4` **1개만 다른 PC로 전달**하면 됩니다. (최초 실행 시 임시 디렉터리에 런타임을 자동 해제하므로 첫 시작 시 약간의 로딩이 있을 수 있습니다.)

### 2. 폴더형 패키지 (Onedir - 실행 속도 빠름)

- **생성 위치**: `dist/rby1-cs-analyzer-v4/`
- **실행**:
  ```bash
  # 웹 UI 런처 실행
  ./dist/rby1-cs-analyzer-v4/rby1-cs-analyzer-v4

  # CLI 분석기 실행
  ./dist/rby1-cs-analyzer-v4/rby1-cs-analyzer-v4-cli --help
  ```
- **배포 방식**: `dist/rby1-cs-analyzer-v4` **폴더 전체(내부 `_internal` 포함)**를 `.tar.gz` 또는 `.zip`으로 압축하여 배포합니다. (`_internal`이 누락되면 실행되지 않습니다.)

---

### 💾 데이터 저장 위치 및 자동 생성 안내

분석 결과 및 업로드된 로그/케이스 데이터는 **프로그램 실행 시 자동으로 생성**되므로, 별도로 `data` 폴더를 복사하거나 만들어 둘 필요가 없습니다.

- **독립 실행 바이너리 실행 시 기본 경로**:
  - **Linux**: `~/.local/share/rby1-cs-analyzer-v4/cases/`
  - **Windows**: `%LOCALAPPDATA%\RB-Y1 CS Analyzer V4\cases\`
- **소스코드/개발 환경 실행 시**: 프로젝트 루트의 `./data/cases/` (존재할 경우)
- **저장 위치 직접 지정**: `--data-root <경로>` 옵션으로 원하는 디렉터리를 지정할 수 있습니다. (지정한 경로도 자동 생성됨)

---

## 릴리즈 배포 패키지 (GitHub Releases)

GitHub Actions가 Ubuntu 22.04 amd64와 Windows x64 패키지를 자동 생성합니다. `v4.1.4`처럼 `v*`
태그를 푸시하면 GitHub Release에 다음 파일과 SHA-256 체크섬이 자동으로 첨부됩니다.

- `rby1-cs-analyzer-v4_4.1.4_ubuntu22.04-amd64.tar.gz`
- `rby1-cs-analyzer-v4_4.1.4_windows-x64.zip`
- `rby1-cs-analyzer-v4_4.1.4_ubuntu22.04-amd64-onefile.tar.gz`
- `rby1-cs-analyzer-v4_4.1.4_windows-x64-onefile.zip`

Ubuntu에서는 압축을 해제한 뒤 `./rby1-cs-analyzer-v4`를 실행하고, Windows에서는 `rby1-cs-analyzer-v4.exe`를 실행합니다.

Ubuntu 패키지는 Ubuntu amd64, Windows 패키지는 Windows x64 전용입니다. Jetson ARM64용은
ARM64 장비 또는 ARM64 runner에서 별도로 빌드해야 합니다.

## 검증

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m ruff check backend tests
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
(cd frontend && npx playwright test)
pyinstaller --clean --noconfirm packaging/rby1-cs-analyzer-v4.spec
packaging/smoke/ubuntu2204_smoke.sh dist/rby1-cs-analyzer-v4 tests/fixtures/representative
pyinstaller --clean --noconfirm --distpath dist-onefile --workpath build-onefile packaging/rby1-cs-analyzer-v4-onefile.spec
packaging/smoke/ubuntu2204_onefile_smoke.sh dist-onefile/rby1-cs-analyzer-v4
```

실제 CS 로그에서 생성한 전체 코퍼스 오라클은 고객 데이터 보호를 위해 저장소에 포함하지 않습니다.
원본 근거는 SHA-256, 파일/압축 멤버, 줄, 바이트 위치로 보존됩니다.

## 모델 자산

3D 시각화에 포함된 RB-Y1 URDF와 mesh는
[RainbowRobotics/rby1-sdk](https://github.com/RainbowRobotics/rby1-sdk)의 모델 자산입니다.
Apache License 2.0에 따라 필요한 파일만 배포하며, 라이선스와 고지문은
`frontend/public/models/licenses/rby1-sdk/`에 포함되어 있습니다.
