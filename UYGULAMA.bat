@echo off
chcp 65001 >nul
cd /d "%~dp0"

set TEMP=%~dp0.tmp
set TMP=%~dp0.tmp
set PIP_CACHE_DIR=%~dp0.pip-cache
set EASYOCR_MODULE_PATH=%~dp0.EasyOCR
set HF_HOME=%~dp0.hf
set PYTHONIOENCODING=utf-8
set PLAKA_SURE=0

if not exist "%~dp0.tmp" mkdir "%~dp0.tmp"
if not exist "%~dp0.EasyOCR" mkdir "%~dp0.EasyOCR"

if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Sanal ortam yok. Once KUR.bat calistirin.
  pause
  exit /b 1
)

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'live_plate_ocr.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
exit
