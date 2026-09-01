# RB-Y1 CS Analyzer V5 - PowerShell Build Script
$ErrorActionPreference = "Stop"

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  RB-Y1 CS Analyzer V5 - Windows Build Script (PS)" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

Set-Location $PSScriptRoot

# 1. Python 버전 확인 (3.10 ~ 3.13 권장)
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "[INFO] Detected Python Version: $pyVer" -ForegroundColor Gray
$isPy314OrHigher = python -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)"
if ($LASTEXITCODE -eq 0) {
    Write-Host "[WARNING] Python 3.14+ detected!" -ForegroundColor Yellow
    Write-Host "[WARNING] rby1-sdk prebuilt binary wheels support Python 3.10, 3.11, 3.12, 3.13." -ForegroundColor Yellow
    Write-Host "[WARNING] If rby1-sdk fails to build, please use Python 3.11 or 3.12." -ForegroundColor Yellow
}

# 2. 가상환경 확인 및 생성
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Host "[1/5] Creating virtual environment .venv..." -ForegroundColor Yellow
    python -m venv .venv
}

Write-Host "[1/5] Activating virtual environment..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# 3. 백엔드 의존성 및 rby1-sdk 설치
Write-Host "[2/5] Installing dependencies and rby1-sdk..." -ForegroundColor Yellow
python -m pip install --upgrade pip
try {
    pip install -e ".[package]"
} catch {
    Write-Host "[WARNING] Package install had warnings, continuing..." -ForegroundColor Yellow
}

# 4. 프론트엔드 UI 빌드 (npm 존재 시 빌드, 부재 시 기존 dist 활용)
Write-Host "[3/5] Checking Frontend UI build..." -ForegroundColor Yellow
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if ($npmCmd) {
    Write-Host "[*] npm found. Building fresh Frontend UI..." -ForegroundColor Green
    Set-Location frontend
    npm run build
    Set-Location ..
} else {
    if (Test-Path "frontend\dist\index.html") {
        Write-Host "[*] npm not found, but prebuilt frontend\dist exists. Using existing UI build." -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Neither npm nor frontend\dist was found!" -ForegroundColor Red
        Write-Host "[ERROR] Please install Node.js from https://nodejs.org/ to build the frontend." -ForegroundColor Red
        exit 1
    }
}

# 5. PyInstaller 단일 실행 파일(.exe) 빌드
Write-Host "[4/5] Building Windows Standalone Executable (.exe)..." -ForegroundColor Yellow
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging\rby1-cs-analyzer-v5.spec

# 6. 루트 디렉토리로 실행 파일 복사
if (Test-Path "dist\rby1-cs-analyzer-v5.exe") {
    Copy-Item "dist\rby1-cs-analyzer-v5.exe" ".\rby1-cs-analyzer-v5.exe" -Force
}

Write-Host ""
Write-Host "===================================================" -ForegroundColor Green
Write-Host "  Build Successful! - Windows V5" -ForegroundColor Green
Write-Host "  - Executable : .\rby1-cs-analyzer-v5.exe" -ForegroundColor Green
Write-Host "  - Dist Output: dist\rby1-cs-analyzer-v5.exe" -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Green
Write-Host "  rby1-cs-analyzer-v5.exe 파일을 실행할 수 있습니다." -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Green
