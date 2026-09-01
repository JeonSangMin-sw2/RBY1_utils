# RB-Y1 CS Analyzer V5 - PowerShell Build Script
$ErrorActionPreference = "Stop"

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  RB-Y1 CS Analyzer V5 - Windows Build Script (PS)" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

Set-Location $PSScriptRoot

# 1. 가상환경 확인 및 생성
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Host "[1/5] Creating virtual environment .venv..." -ForegroundColor Yellow
    python -m venv .venv
}

Write-Host "[1/5] Activating virtual environment..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# 2. 백엔드 의존성 및 rby1-sdk 설치
Write-Host "[2/5] Installing dependencies and rby1-sdk..." -ForegroundColor Yellow
python -m pip install --upgrade pip
pip install -e ".[package]"

# 3. 프론트엔드 UI 빌드
Write-Host "[3/5] Building Frontend UI..." -ForegroundColor Yellow
Set-Location frontend
npm run build
Set-Location ..

# 4. PyInstaller 단일 실행 파일(.exe) 빌드
Write-Host "[4/5] Building Windows Standalone Executable (.exe)..." -ForegroundColor Yellow
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging\rby1-cs-analyzer-v5.spec

# 5. 루트 디렉토리로 실행 파일 복사
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
