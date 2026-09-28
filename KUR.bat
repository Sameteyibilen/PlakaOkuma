@echo off
chcp 65001 >nul
cd /d D:\PlakaOkuma
title Plaka Okuma - Kurulum

set TEMP=D:\PlakaOkuma\.tmp
set TMP=D:\PlakaOkuma\.tmp
set PIP_CACHE_DIR=D:\PlakaOkuma\.pip-cache
set EASYOCR_MODULE_PATH=D:\PlakaOkuma\.EasyOCR
set HF_HOME=D:\PlakaOkuma\.hf

if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist "%EASYOCR_MODULE_PATH%" mkdir "%EASYOCR_MODULE_PATH%"

echo === D:\PlakaOkuma kurulum ===
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
