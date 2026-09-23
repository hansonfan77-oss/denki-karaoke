@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === DENKI 伴奏工具 (診斷模式) ===
echo.
"C:\denki-karaoke\venv\Scripts\python.exe" -m karaoke.app --debug
echo.
echo ==== log ====
powershell -NoProfile -Command "Get-Content -Tail 40 -Encoding utf8 C:\denki-karaoke\app.log"
pause
