# DENKI 伴奏工具 — 更新並測試
# 由「更新並測試.bat」呼叫。做三件事：取得最新版 → 補裝套件 → 跑自動測試，結果寫到 C:\denki-karaoke\結果.txt

$ErrorActionPreference = "Continue"
$app  = $PSScriptRoot
$root = "C:\denki-karaoke"
$vpy  = "$root\venv\Scripts\python.exe"
$repo = "https://github.com/hansonfan77-oss/denki-karaoke.git"
Set-Location $app

function Say($msg) { Write-Host ""; Write-Host "==== $msg ====" -ForegroundColor Cyan }

# ---------- 1. 取得最新版 ----------
Say "1/3 取得最新版"
if (Get-Command git -ErrorAction SilentlyContinue) {
  if (Test-Path "$app\.git") {
    git -C $app pull --ff-only
  } else {
    # 第一次：把解壓縮出來的資料夾接上 GitHub，之後就能自動更新
    git -C $app init -q
    git -C $app remote add origin $repo 2>$null
    git -C $app fetch -q origin main 2>$null
    if ($LASTEXITCODE -eq 0) {
      git -C $app reset -q --hard origin/main
      git -C $app branch -q -M main
      git -C $app branch -q --set-upstream-to=origin/main main 2>$null
      Write-Host "已連上 GitHub，之後會自動更新。"
    } else {
      Write-Host "GitHub 上還沒有程式或暫時連不上，先用目前資料夾的版本。" -ForegroundColor Yellow
      Remove-Item -Recurse -Force "$app\.git" -ErrorAction SilentlyContinue
    }
  }
} else {
  Write-Host "這台電腦沒有 git，使用目前資料夾的版本。" -ForegroundColor Yellow
}

# ---------- 2. 補裝套件 ----------
Say "2/3 檢查套件"
if (-not (Test-Path $vpy)) {
  Write-Host "找不到 $vpy，請先執行 setup.ps1 安裝環境。" -ForegroundColor Red
  Read-Host "按 Enter 結束"
  exit 1
}
& $vpy -m pip install -q -r "$app\requirements.txt"
# 高品質／保留和聲模式：audio-separator 本體不拉相依（避開沒有 Windows 版的 diffq、也不動顯卡版 PyTorch）
& $vpy -m pip install -q -r "$app\requirements-roformer.txt"
& $vpy -m pip install -q --no-deps "audio-separator==0.47.0"

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

# ---------- 桌面捷徑（沒有才建立） ----------
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "DENKI 伴奏工具.lnk"
if (-not (Test-Path $lnk)) {
  try {
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($lnk)
    $sc.TargetPath = "$root\venv\Scripts\pythonw.exe"
    $sc.Arguments = "-m karaoke.app"
    $sc.WorkingDirectory = $app
    $sc.Description = "DENKI 伴奏工具"
    $sc.Save()
    Write-Host "已在桌面建立「DENKI 伴奏工具」捷徑。" -ForegroundColor Green
  } catch {
    Write-Host "建立桌面捷徑失敗（不影響使用，可以改點 開啟伴奏工具.bat）" -ForegroundColor Yellow
  }
}

# ---------- 3. 自動測試 ----------
Say "3/3 自動測試（含一首真實歌曲的 AI 分離；第一次用高品質模式會先下載約 600 MB 模型）"
& $vpy -m tests.selftest --real
Say "完成"
if (Test-Path "$root\結果.txt") { Start-Process notepad.exe "$root\結果.txt" }
