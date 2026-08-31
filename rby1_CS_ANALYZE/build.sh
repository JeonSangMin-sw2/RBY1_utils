#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. 가상환경 활성화 (.venv 존재 시)
if [[ -d ".venv" && -z "${VIRTUAL_ENV:-}" ]]; then
  echo "==> Activating virtual environment (.venv)..."
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "==> [1/4] Building Frontend UI..."
npm --prefix frontend run build

echo "==> [2/4] Building Standalone Onefile Binary (V5)..."
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging/rby1-cs-analyzer-v5.spec

echo "==> [3/4] Ensuring Executable Permissions (0755)..."
chmod 755 dist/rby1-cs-analyzer-v5

echo "==> [4/4] Packaging into tar.gz (Preserving Linux Permissions)..."
ARCH="$(uname -m)"
PACKAGE_NAME="rby1-cs-analyzer-v5-linux-${ARCH}.tar.gz"
tar -czvf "dist/${PACKAGE_NAME}" -C dist rby1-cs-analyzer-v5

# 현재 루트 경로로 실행파일 및 압축본 복사 (바로 실행 및 전달 가능)
cp dist/rby1-cs-analyzer-v5 ./rby1-cs-analyzer-v5
cp "dist/${PACKAGE_NAME}" "./${PACKAGE_NAME}"
chmod 755 ./rby1-cs-analyzer-v5

echo ""
echo "================================================================="
echo "🎉 Build & Packaging Successful (V5)!"
echo "  - Root Binary   : ./rby1-cs-analyzer-v5  (현재 폴더에서 바로 실행 가능)"
echo "  - Root Archive  : ./${PACKAGE_NAME}"
echo "  - Dist Backup   : dist/rby1-cs-analyzer-v5"
echo "================================================================="
echo "💡 현재 경로에서 바로 './rby1-cs-analyzer-v5'를 실행하거나,"
echo "   '${PACKAGE_NAME}' 파일을 다른 PC로 전달하여 사용하실 수 있습니다."


