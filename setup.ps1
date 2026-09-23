# DENKI 伴奏工具 — 第 0 步自動安裝與測試
# 用法：powershell -ExecutionPolicy Bypass -File C:\denki-karaoke\app\setup.ps1
# 裝好之後改用「更新並測試.bat」做日常更新與測試
# 全程紀錄在 C:\denki-karaoke\setup-log.txt，結果摘要在 C:\denki-karaoke\結果.txt

$ErrorActionPreference = "Continue"
$root = "C:\denki-karaoke"
New-Item -ItemType Directory -Force -Path $root | Out-Null
Set-Location $root
Start-Transcript -Path "$root\setup-log.txt" -Append | Out-Null

$report = @()
function Say($msg) { Write-Host ""; Write-Host "==== $msg ====" -ForegroundColor Cyan }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Find-Python {
  # 只接受 3.11 或 3.12（Demucs 對 3.13 支援不完整）
  foreach ($c in @("py -3.11", "py -3.12")) {
    $exe, $arg = $c.Split(" ", 2)
    try {
      $v = & $exe $arg --version 2>&1
      if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3\.1[12]") { return $c }
    } catch {}
  }
  return $null
}

# 把腳本旁邊的測試歌曲先搬進專案資料夾（如果有的話）
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($here -and ($here -ne $root)) {
  Get-ChildItem $here -File | Where-Object { $_.Extension -in ".mp3", ".wav", ".flac", ".m4a", ".mp4" } |
    ForEach-Object { Copy-Item $_.FullName -Destination $root -Force }
}

# ---------- 1. Python ----------
Say "1/6 檢查 Python"
$pyCmd = Find-Python
if (-not $pyCmd) {
  Write-Host "沒找到 Python，用 winget 安裝 3.11..."
  winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements --silent
  Refresh-Path
  $pyCmd = Find-Python
}
if (-not $pyCmd) { $report += "Python：安裝失敗"; Write-Host "Python 安裝失敗，停止。" -ForegroundColor Red; Stop-Transcript; exit 1 }
$pyExe, $pyArg = $pyCmd.Split(" ", 2)
$report += "Python：OK（$(& $pyExe $pyArg --version 2>&1)）"

# ---------- 2. FFmpeg ----------
Say "2/6 檢查 FFmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "沒找到 FFmpeg，用 winget 安裝..."
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent
  Refresh-Path
}
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
  $hasRb = (ffmpeg -hide_banner -filters 2>&1 | Select-String "rubberband") -ne $null
  $report += "FFmpeg：OK（rubberband 變調濾鏡：$(if ($hasRb) {'有'} else {'沒有'})）"
} else {
  $report += "FFmpeg：安裝失敗"
  $hasRb = $false
}

# ---------- 3. 虛擬環境 ----------
Say "3/6 建立虛擬環境"
# 舊的 venv 若不是 3.11/3.12 就砍掉重建
if (Test-Path "$root\venv\Scripts\python.exe") {
  $vv = & "$root\venv\Scripts\python.exe" --version 2>&1
  if ("$vv" -notmatch "Python 3\.1[12]") {
    Write-Host "舊環境是 $vv，刪除重建..."
    Remove-Item -Recurse -Force "$root\venv"
  }
}
if (-not (Test-Path "$root\venv\Scripts\python.exe")) {
  & $pyExe $pyArg -m venv "$root\venv"
}
$vpy = "$root\venv\Scripts\python.exe"
$vpip = "$root\venv\Scripts\pip.exe"
& $vpy -m pip install --upgrade pip --quiet

# ---------- 4. PyTorch + Demucs ----------
Say "4/6 安裝 PyTorch（顯卡版，約 2.5GB，最久的一步）與 Demucs"
& $vpip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
& $vpip install numpy demucs
# 高品質／保留和聲模式（詳見 requirements-roformer.txt 的說明）
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path "$appDir\requirements-roformer.txt") {
  & $vpip install -r "$appDir\requirements-roformer.txt"
  & $vpip install --no-deps "audio-separator==0.47.0"
}

# ---------- 顯卡版 PyTorch 守門 ----------
# 2.1 版的 onnx2torch 曾經帶進 torchvision，把顯卡版 PyTorch 換成 CPU 版。先移除這兩個用不到的套件，
# 再檢查：有 NVIDIA 顯卡但 PyTorch 不是顯卡版 → 自動換回來。
& $vpy -m pip uninstall -y -q onnx2torch-py313 torchvision 2>$null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  $cudaBuild = & $vpy -c "import torch; print('yes' if torch.version.cuda else 'no')" 2>$null
  if ($cudaBuild -ne "yes") {
    Write-Host "偵測到 NVIDIA 顯卡，但 PyTorch 是 CPU 版 → 重新安裝顯卡版（約 2.5 GB，要等一段時間，請不要關掉視窗）" -ForegroundColor Yellow
    & $vpy -m pip install --force-reinstall --no-deps torch torchaudio --index-url https://download.pytorch.org/whl/cu128
    $gpuOk = & $vpy -c "import torch; print(torch.cuda.is_available())" 2>$null
    if ($gpuOk -eq "True") { Write-Host "顯卡版 PyTorch 已裝回。" -ForegroundColor Green }
    else { Write-Host "顯卡版 PyTorch 安裝後仍然抓不到顯卡，請把這個視窗截圖給 Claude。" -ForegroundColor Red }
  }
}
$demucsOk = $false
if (Test-Path "$root\venv\Scripts\demucs.exe") {
  & $vpy -c "import numpy, torch, demucs" 2>$null
  $demucsOk = ($LASTEXITCODE -eq 0)
}
$report += "Demucs：$(if ($demucsOk) {'OK'} else {'安裝失敗'})"

# ---------- 5. 顯卡 ----------
Say "5/6 檢查顯卡"
$gpu = & $vpy -c "import torch; ok=torch.cuda.is_available(); print('CUDA=' + str(ok)); print('GPU=' + (torch.cuda.get_device_name(0) if ok else 'none'))" 2>&1
Write-Host $gpu
$report += "顯卡：$($gpu -join ' / ')"

# ---------- 6. 分離 + 變調 ----------
Say "6/6 分離人聲並變調"
$test = Get-ChildItem $root -File | Where-Object { $_.Extension -in ".mp3", ".wav", ".flac", ".m4a", ".mp4" } | Select-Object -First 1
if (-not $test) {
  $report += "測試歌曲：資料夾裡沒有音檔，跳過分離。下次把一首 mp3 丟進 $root 再跑一次腳本即可。"
} elseif (-not $demucsOk) {
  $report += "測試歌曲：因 Demucs 未安裝成功而跳過"
} else {
  Write-Host "使用：$($test.Name)"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & "$root\venv\Scripts\demucs.exe" --two-stems=vocals -n htdemucs_ft -o "$root\separated" $test.FullName
  $sw.Stop()
  $stem = [IO.Path]::GetFileNameWithoutExtension($test.Name)
  $nv = "$root\separated\htdemucs_ft\$stem\no_vocals.wav"
  if (Test-Path $nv) {
    $report += "分離：OK，用時 $([math]::Round($sw.Elapsed.TotalSeconds)) 秒（含第一次下載模型）"
    $report += "  伴奏：$nv"
    $report += "  人聲：$root\separated\htdemucs_ft\$stem\vocals.wav"
    if ($hasRb) {
      ffmpeg -y -hide_banner -loglevel error -i $nv -af "rubberband=pitch=0.8409" "$root\${stem}_伴奏_-3key.wav"
      ffmpeg -y -hide_banner -loglevel error -i $nv -af "rubberband=pitch=1.3348" "$root\${stem}_伴奏_+5key.wav"
      $report += "變調：OK"
      $report += "  降 3 Key：$root\${stem}_伴奏_-3key.wav"
      $report += "  升 5 Key：$root\${stem}_伴奏_+5key.wav"
    } else {
      $report += "變調：跳過（FFmpeg 沒有 rubberband 濾鏡）"
    }
  } else {
    $report += "分離：失敗，請看 setup-log.txt 最後的紅字"
  }
}

# ---------- 結果 ----------
$out = @("DENKI 伴奏工具 第 0 步結果  $(Get-Date -Format 'yyyy-MM-dd HH:mm')", "") + $report
$out | Out-File "$root\結果.txt" -Encoding utf8
Say "完成"
$out | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Start-Process explorer.exe $root
