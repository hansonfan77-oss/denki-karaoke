@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 找這份程式用的 Python：安裝程式放的 ..\python，或舊的開發環境 ..\venv（都用相對位置，整個資料夾搬走也能用）
set "PYW=%~dp0..\python\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0..\venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo 找不到 Python，請先執行「安裝.bat」。
  pause
  exit /b 1
)
start "" "%PYW%" -m karaoke.app
