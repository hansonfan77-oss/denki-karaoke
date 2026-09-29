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
echo === DENKI 伴奏工具 (診斷模式) ===
echo.
"%PY%" -m karaoke.app --debug
echo.
echo ==== log ====
powershell -NoProfile -Command "Get-Content -Tail 40 -Encoding utf8 '%~dp0..\app.log'"
echo.
echo ==== freeze.log ====
powershell -NoProfile -Command "if (Test-Path '%~dp0..\freeze.log') { Get-Content -Tail 60 -Encoding utf8 '%~dp0..\freeze.log' }"
pause
