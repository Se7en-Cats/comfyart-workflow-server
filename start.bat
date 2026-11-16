@echo off
cd /d "%~dp0"
title ComfyArt Workflow Studio

REM ---- 1. create venv on first run ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] First run: creating venv...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to create venv.
        echo Make sure python is installed and on PATH: run python --version in cmd.
        pause
        exit /b 1
    )
)

REM ---- 2. install deps ----
echo [2/3] Checking dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency install failed. Check network and retry.
    pause
    exit /b 1
)

REM ---- 3. start server ----
echo [3/3] Starting...
echo.
echo ============================================================
echo   ComfyArt Workflow Studio
echo   URL: http://127.0.0.1:8811
echo   Close this window to quit.
echo ============================================================
echo.

start "" http://127.0.0.1:8811
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8811

echo.
echo Server stopped.
pause
