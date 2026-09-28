@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Kamera izleme

set TEMP=%~dp0.tmp
set TMP=%~dp0.tmp
set PYTHONIOENCODING=utf-8

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Once KUR.bat calistirin.
  pause
  exit /b 1
)

echo.
echo 4 kamera - tarayici eklentisi yok.
echo Cikis: pencerede Q
echo.
"%~dp0.venv\Scripts\python.exe" -u "%~dp0izle_kameralar.py"
pause
