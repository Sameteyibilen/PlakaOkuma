@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Plaka Okuma - Kurulum

set TEMP=%~dp0.tmp
set TMP=%~dp0.tmp
set PIP_CACHE_DIR=%~dp0.pip-cache
set EASYOCR_MODULE_PATH=%~dp0.EasyOCR
set HF_HOME=%~dp0.hf

if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist "%EASYOCR_MODULE_PATH%" mkdir "%EASYOCR_MODULE_PATH%"

echo === %~dp0 kurulum ===
where python >nul 2>&1
if errorlevel 1 (
  echo Python bulunamadi.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Sanal ortam olusturuluyor...
  python -m venv .venv
)

echo Bagimliliklar yukleniyor...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Bitti. CALISTIR.bat ile baslatin.
pause
