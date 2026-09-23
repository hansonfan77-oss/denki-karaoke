@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\denki-karaoke\venv\Scripts\python.exe" -m karaoke wizard %1
pause
