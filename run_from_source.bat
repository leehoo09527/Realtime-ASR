@echo off
REM Run the app directly from source (no packaging). Needs .venv or a python with deps.
REM Usage: double-click, or:  run_from_source.bat
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

REM Tell asr_engine where the models are (package-local models\ folder)
set "ASR_MODEL_ROOT=%~dp0models"

echo Using python: %PY%
echo Models      : %ASR_MODEL_ROOT%
echo Data dir    : %~dp0src\data  (dev mode writes transcripts here)
echo.
"%PY%" src\app.py
if errorlevel 1 pause
