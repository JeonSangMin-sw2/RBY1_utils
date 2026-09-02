@echo off
setlocal

echo ===================================================
echo   RB-Y1 CS Analyzer V5 - Windows Build Script
echo ===================================================

cd /d "%~dp0"

:: 0. 이전 실행 중인 프로세스 자동 종료 및 기존 파일 잠금 해제
echo [*] Closing any running analyzer instances...
taskkill /F /IM rby1-cs-analyzer-v5.exe 2>nul
del /f /q "dist\rby1-cs-analyzer-v5.exe" 2>nul
del /f /q "rby1-cs-analyzer-v5.exe" 2>nul

:: 1. Python 버전 확인 (3.10 ~ 3.13 권장)
python -c "import sys; sys.exit(0 if sys.version_info < (3, 14) else 1)" 2>nul
if errorlevel 1 (
    echo [WARNING] Python 3.14+ detected!
    echo [WARNING] rby1-sdk prebuilt binary wheels support Python 3.10, 3.11, 3.12, 3.13.
    echo [WARNING] If rby1-sdk build fails, please use Python 3.11 or 3.12.
    echo.
)

:: 2. 가상환경 확인 및 생성
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

:: 3. 백엔드 의존성 및 rby1-sdk 설치
echo [2/5] Installing dependencies and rby1-sdk...
python -m pip install --upgrade pip
pip install -e ".[package]"
if errorlevel 1 (
    echo [WARNING] Package install had warnings or rby1-sdk source build skipped.
    echo [INFO] Continuing build...
)

:: Node.js 로컬 경로 확인 및 PATH 등록
if exist "%USERPROFILE%\.local\node\node.exe" (
    set "PATH=%USERPROFILE%\.local\node;%PATH%"
)

:: 4. 프론트엔드 UI 빌드 (npm 존재 시 빌드, 부재 시 기존 dist 활용)
echo [3/5] Checking Frontend UI build...
where npm >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] npm found. Building fresh Frontend UI...
    cd frontend
    call npm run build
    if errorlevel 1 (
        echo [ERROR] Frontend build failed!
        cd ..
        pause
        exit /b 1
    )
    cd ..
) else (
    if exist "frontend\dist\index.html" (
        if exist "frontend\dist\assets\*.js" (
            echo [*] npm not found, but valid prebuilt frontend\dist exists. Using existing UI build.
        ) else (
            echo [ERROR] frontend\dist\index.html exists but frontend\dist\assets\ is missing or empty!
            echo [ERROR] Please install Node.js or ensure prebuilt assets are synced.
            pause
            exit /b 1
        )
    ) else (
        echo [ERROR] Neither npm nor frontend\dist was found!
        echo [ERROR] Please install Node.js from https://nodejs.org/ to build the frontend.
        pause
        exit /b 1
    )
)

:: 5. PyInstaller 단일 실행 파일(.exe) 빌드
echo [4/5] Building Windows Standalone Executable (.exe)...
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging\rby1-cs-analyzer-v5.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

:: 6. 루트 디렉토리로 실행 파일 복사
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
