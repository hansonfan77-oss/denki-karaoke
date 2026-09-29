@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0..\python\python.exe"
if not exist "%PY%" set "PY=%~dp0..\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo 找不到 Python，請先執行「安裝.bat」。
  pause
  exit /b 1
)
"%PY%" -m karaoke wizard %1
pause
