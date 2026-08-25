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

## 폴더 구조 및 실행

```text
rby1_CS_ANALYZE/
├── config/                      # 오류 가이드 및 규칙 설정 (error_guide.yaml)
├── data/                        # 통합 데이터셋 및 분석 케이스 저장소 (cases/)
├── frontend/                    # React / Vite 프론트엔드 UI
├── backend/                     # Python 장애 분석 백엔드 패키지
└── main.py                      # 루트 메인 실행 파일
```

### 실행 방법

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
npm --prefix frontend ci
npm --prefix frontend run build

# 루트 실행 파일로 실행 (권장)
python main.py

# 또는 모듈 직접 실행 시 (backend 경로 지정)
PYTHONPATH=backend python -m rby1_analyzer.launcher
```

Chrome/Chromium이 설치되어 있으면 주소창이 없는 독립 프로그램 창으로 열립니다. Chrome이 없으면
기본 브라우저로 열립니다. URL만 출력하려면 `--no-open-browser`를 사용하십시오.

## 배포 패키지

GitHub Actions가 Ubuntu 22.04 amd64와 Windows x64 패키지를 생성합니다. `v4.1.4`처럼 `v*`
태그를 푸시하면 GitHub Release에 다음 파일과 SHA-256 체크섬이 자동으로 첨부됩니다.

- `rby1-cs-analyzer-v4_4.1.4_ubuntu22.04-amd64.tar.gz`
- `rby1-cs-analyzer-v4_4.1.4_windows-x64.zip`
- `rby1-cs-analyzer-v4_4.1.4_ubuntu22.04-amd64-onefile.tar.gz`
- `rby1-cs-analyzer-v4_4.1.4_windows-x64-onefile.zip`

Ubuntu에서는 압축을 완전히 해제한 뒤 다음 파일을 실행합니다.

```bash
./rby1-cs-analyzer-v4
```

Windows에서는 압축을 완전히 해제한 뒤 `rby1-cs-analyzer-v4.exe`를 실행합니다. 배포본은
PyInstaller `onedir` 형식이므로 실행 파일과 같은 위치의 `_internal` 폴더가 반드시 필요합니다.
실행 파일만 따로 복사하지 말고 압축에서 나온 패키지 폴더 전체를 유지해야 합니다. Python과
Node.js는 별도로 설치하지 않아도 됩니다.

파일명에 `onefile`이 포함된 패키지는 압축을 해제하면 실행 파일 하나만 생성됩니다. 이 버전은
`_internal` 폴더 없이 실행할 수 있으며, 최초 실행 시 임시 디렉터리에 런타임을 푸는 과정 때문에
폴더형 패키지보다 시작이 다소 느릴 수 있습니다.

Ubuntu 패키지는 Ubuntu amd64, Windows 패키지는 Windows x64 전용입니다. Jetson ARM64용은
ARM64 장비 또는 ARM64 runner에서 별도로 빌드해야 합니다.

## 검증

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m ruff check src tests
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
