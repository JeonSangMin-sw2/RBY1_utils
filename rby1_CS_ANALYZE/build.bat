@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo   RB-Y1 CS Analyzer V5 - Windows Build Script
echo ===================================================

cd /d "%~dp0"

:: 1. 가상환경 확인 및 활성화
if exist ".venv\Scripts\activate.bat" (
    echo ==^> [1/4] Activating virtual environment (.venv)...
    call .venv\Scripts\activate.bat
) else (
    echo ==^> [1/4] Virtual environment not found. Using system Python...
)

:: 2. 프론트엔드 UI 빌드
echo ==^> [2/4] Building Frontend UI...
cd frontend
call npm run build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Frontend build failed!
    exit /b %ERRORLEVEL%
)
cd ..

:: 3. PyInstaller 단일 실행 파일(.exe) 빌드
echo ==^> [3/4] Building Windows Standalone Executable (.exe)...
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging\rby1-cs-analyzer-v5.spec
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed!
    exit /b %ERRORLEVEL%
)

:: 4. 루트 디렉토리로 실행 파일 복사
if exist "dist\rby1-cs-analyzer-v5.exe" (
    copy /y "dist\rby1-cs-analyzer-v5.exe" ".\rby1-cs-analyzer-v5.exe" >nul
)

echo.
echo ===================================================
echo   Build Successful! (Windows V5)
echo   - Executable: .\rby1-cs-analyzer-v5.exe
echo   - Dist Output: dist\rby1-cs-analyzer-v5.exe
echo ===================================================
echo   'rby1-cs-analyzer-v5.exe'를 더블 클릭하여 실행할 수 있습니다.
echo ===================================================

pause
