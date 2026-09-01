@echo off
setlocal

echo ===================================================
echo   RB-Y1 CS Analyzer V5 - Windows Build Script
echo ===================================================

cd /d "%~dp0"

:: 1. 가상환경 확인 및 생성
if exist ".venv\Scripts\activate.bat" goto :activate_env

echo [1/5] Creating virtual environment .venv...
python -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment!
    pause
    exit /b 1
)

:activate_env
echo [1/5] Activating virtual environment...
call .venv\Scripts\activate.bat

:: 2. 백엔드 의존성 및 rby1-sdk 설치
echo [2/5] Installing dependencies and rby1-sdk...
python -m pip install --upgrade pip
pip install -e ".[package]"
if errorlevel 1 (
    echo [WARNING] Package install had warnings, continuing...
)

:: 3. 프론트엔드 UI 빌드
echo [3/5] Building Frontend UI...
cd frontend
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed!
    cd ..
    pause
    exit /b 1
)
cd ..

:: 4. PyInstaller 단일 실행 파일(.exe) 빌드
echo [4/5] Building Windows Standalone Executable (.exe)...
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging\rby1-cs-analyzer-v5.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

:: 5. 루트 디렉토리로 실행 파일 복사
if exist "dist\rby1-cs-analyzer-v5.exe" (
    copy /y "dist\rby1-cs-analyzer-v5.exe" ".\rby1-cs-analyzer-v5.exe" >nul
)

echo.
echo ===================================================
echo   Build Successful! - Windows V5
echo   - Executable: .\rby1-cs-analyzer-v5.exe
echo   - Dist Output: dist\rby1-cs-analyzer-v5.exe
echo ===================================================
echo   rby1-cs-analyzer-v5.exe 파일을 실행할 수 있습니다.
echo ===================================================

pause
