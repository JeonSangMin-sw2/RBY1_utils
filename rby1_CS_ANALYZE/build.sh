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

echo ""
echo "================================================================="
echo "🎉 Build & Packaging Successful (V5)!"
echo "  - Single Binary : dist/rby1-cs-analyzer-v5"
echo "  - Release Archive: dist/${PACKAGE_NAME}"
echo "================================================================="
echo "💡 다른 PC로 전달할 때는 'dist/${PACKAGE_NAME}' 파일을 전달하시면"
echo "   압축 해제 시 chmod 없이 바로 실행 가능합니다."
