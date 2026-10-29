@echo off
REM ============================================================
REM  Real-time ASR - Windows build script
REM  Run from anywhere: double-click, or execute in cmd.
REM  Steps: venv -> pip install -> self check -> pyinstaller
REM  Output: dist_win\实时语音转写\实时语音转写.exe
REM  NOTE: messages are ASCII on purpose (cmd code page safety).
REM ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo ==== [0/4] preflight ====
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3.11/3.12 x64 first,
  echo         and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)
python -c "import sys;print('python',sys.version.split()[0],sys.maxsize>2**32 and '64bit' or '32bit')"

if not exist "models\sense-voice\model.int8.onnx" (
  echo [ERROR] models\sense-voice\model.int8.onnx not found.
  echo         Run:  powershell -ExecutionPolicy Bypass -File tools\download_models.ps1
  echo         Or copy the models folder from the Mac.
  pause
  exit /b 1
)

echo.
echo ==== [1/4] virtual env ====
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo .venv already exists, reuse it.
)
set "PY=.venv\Scripts\python.exe"

echo.
echo ==== [2/4] pip install -r requirements.txt ====
"%PY%" -m pip install -U pip
if errorlevel 1 goto :fail
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo ==== [3/4] self check ====
"%PY%" tools\check_env.py
if errorlevel 1 (
  echo [WARN] self check reported problems. Fix them before packaging,
  echo        otherwise the exe will start but fail to recognize.
  pause
)

echo.
echo ==== [4/4] pyinstaller ====
if exist "dist_win" (
  echo [INFO] dist_win exists. If you see stale output, rename it first:
  echo        ren dist_win dist_win_old
)
"%PY%" -m PyInstaller --noconfirm --distpath dist_win --workpath build_win asr-win.spec
if errorlevel 1 goto :fail

echo.
echo ==== DONE ====
echo Output: dist_win\^<app^>\^<app^>.exe  (folder + exe are named in Chinese)
echo Verify: run that exe, click Start, speak one sentence.
pause
exit /b 0

:fail
echo.
echo [FAILED] see the log above.
pause
exit /b 1
