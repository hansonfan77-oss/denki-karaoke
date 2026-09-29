@echo off
chcp 65001 >nul
title DENKI 伴奏工具 安裝程式
rem 解壓縮後點兩下這個檔案。實際的安裝步驟在 app\installer\install.ps1。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\installer\install.ps1" %*
