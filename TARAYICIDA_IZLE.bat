@echo off
chcp 65001 >nul
cd /d D:\PlakaOkuma
title Kameralar - Edge

set TEMP=D:\PlakaOkuma\.tmp
set TMP=D:\PlakaOkuma\.tmp
set PYTHONIOENCODING=utf-8

if not exist "D:\PlakaOkuma\.tmp" mkdir "D:\PlakaOkuma\.tmp"
if not exist "D:\PlakaOkuma\.venv\Scripts\python.exe" (
  echo Once KUR.bat calistirin.
  pause
  exit /b 1
)

wmic process where "commandline like '%%web_izle.py%%'" call terminate >nul 2>&1

start "Plaka kameralar" /min "D:\PlakaOkuma\.venv\Scripts\python.exe" -u "D:\PlakaOkuma\web_izle.py"
timeout /t 2 /nobreak >nul

start "" msedge "http://127.0.0.1:8765"

echo.
echo Edge acildi: http://127.0.0.1:8765
echo Kameranin kendi adresi (172.16.21.165) Edge'de CANLI YAYIN ACMAZ.
echo Bu pencereyi kapatmak izlemeyi durdurur.
echo.
pause

wmic process where "commandline like '%%web_izle.py%%'" call terminate >nul 2>&1
