@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Kamera baglanti testi

set TEMP=%~dp0.tmp
set TMP=%~dp0.tmp
set PYTHONIOENCODING=utf-8

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Once KUR.bat calistirin.
  pause
  exit /b 1
)

echo.
echo 4 kamera RTSP testi (tek kare).
echo.
"%~dp0.venv\Scripts\python.exe" -u "%~dp0test_cameras.py"
pause
