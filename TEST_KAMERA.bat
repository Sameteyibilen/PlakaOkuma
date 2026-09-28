@echo off
chcp 65001 >nul
cd /d D:\PlakaOkuma
title Kamera baglanti testi

set TEMP=D:\PlakaOkuma\.tmp
set TMP=D:\PlakaOkuma\.tmp
set PYTHONIOENCODING=utf-8

if not exist "D:\PlakaOkuma\.venv\Scripts\python.exe" (
  echo Once KUR.bat calistirin.
  pause
  exit /b 1
)

echo.
echo 4 kamera RTSP testi (tek kare).
echo.
"D:\PlakaOkuma\.venv\Scripts\python.exe" -u "D:\PlakaOkuma\test_cameras.py"
pause
