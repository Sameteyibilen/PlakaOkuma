@echo off
chcp 65001 >nul
cd /d D:\PlakaOkuma

set TEMP=D:\PlakaOkuma\.tmp
set TMP=D:\PlakaOkuma\.tmp
set PIP_CACHE_DIR=D:\PlakaOkuma\.pip-cache
set EASYOCR_MODULE_PATH=D:\PlakaOkuma\.EasyOCR
set HF_HOME=D:\PlakaOkuma\.hf
set PYTHONIOENCODING=utf-8
set PLAKA_SURE=0

if not exist "D:\PlakaOkuma\.tmp" mkdir "D:\PlakaOkuma\.tmp"
if not exist "D:\PlakaOkuma\.EasyOCR" mkdir "D:\PlakaOkuma\.EasyOCR"

if not exist "D:\PlakaOkuma\.venv\Scripts\pythonw.exe" (
  echo Sanal ortam yok. Once KUR.bat calistirin.
  pause
  exit /b 1
)

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'live_plate_ocr.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

start "" "D:\PlakaOkuma\.venv\Scripts\pythonw.exe" "D:\PlakaOkuma\app.py"
exit
