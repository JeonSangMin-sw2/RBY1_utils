@echo off
setlocal
cd /d "%~dp0"

:: 윈도우 웹 다운로드 보안 차단(Mark of the Web / SmartScreen) 자동 해제
powershell -NoProfile -Command "Unblock-File -Path '.\rby1-cs-analyzer-v5.exe' -ErrorAction SilentlyContinue" 2>nul

:: 프로그램 실행
start "" ".\rby1-cs-analyzer-v5.exe"
