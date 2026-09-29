# DENKI 伴奏工具 — 檢查套件並測試
# 由「更新並測試.bat」呼叫。做兩件事：照鎖定清單補裝套件 → 跑自動測試，結果寫到 <安裝資料夾>\結果.txt
# （第 3 批起，正式版本由程式內的「檢查更新」自動更新；這個檔案用在手動換上測試版之後的檢查）

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$app  = $PSScriptRoot
$root = Split-Path -Parent $app
$env:PYTHONIOENCODING = "utf-8"
Set-Location $app

function Say($msg) { Write-Host ""; Write-Host "==== $msg ====" -ForegroundColor Cyan }

# 這份程式用的 Python：安裝程式放的 python\，或舊的開發環境 venv\
$py = Join-Path $root "python\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $root "venv\Scripts\python.exe" }
if (-not (Test-Path $py)) {
  Write-Host "找不到 Python，請先執行「安裝.bat」。" -ForegroundColor Red
  exit 1
}
Write-Host "安裝資料夾：$root"
Write-Host "Python：$py"

# ---------- 1. 補裝套件 ----------
Say "1/2 照鎖定清單檢查套件（有不一樣的會自動裝成正確版本）"
& $py -m karaoke.deps
if ($LASTEXITCODE -ne 0) {
  Write-Host "套件沒有全部裝好，請把這個視窗截圖給 Claude。" -ForegroundColor Red
}

# ---------- 2. 自動測試 ----------
Say "2/2 自動測試（含一首真實歌曲的 AI 分離；第一次用高品質模式會先下載約 600 MB 模型）"
& $py -m tests.selftest --real
Say "完成"
$result = Join-Path $root "結果.txt"
if (Test-Path $result) { Start-Process notepad.exe $result }
