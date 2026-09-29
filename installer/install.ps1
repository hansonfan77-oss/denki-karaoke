# DENKI 伴奏工具 安裝程式（由「安裝.bat」呼叫；Windows 10/11 內建的 PowerShell 5.1 就能跑）
#
# 裝好的資料夾（整個資料夾就是全部，搬到別的磁碟也能用）：
#   <安裝位置>\
#     app\          程式本體（程式內更新只換這個資料夾）
#     python\       Python 3.11（嵌入式版本，不動到電腦上其他 Python）
#     ffmpeg\bin\   FFmpeg（含 rubberband 變調濾鏡）
#     songs\ models\ settings.json   使用中產生的資料
#     解除安裝.bat   install-log.txt
#
# 每一步都會先檢查「已經裝好就跳過」，所以中途失敗（例如網路斷掉）再點一次 安裝.bat 會從中斷的地方繼續。
#
# 參數（一般不用）：
#   -Target <資料夾>   不跳選擇視窗，直接裝在這裡
#   -Cpu               有 NVIDIA 顯卡也裝 CPU 版
#   -Quiet             不問問題（自動化測試用）

param(
  [string]$Target = "",
  [switch]$Cpu,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # PowerShell 5.1 內建的進度條會讓下載慢 10 倍
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# ------------------------------------------------------------------ 固定的版本與下載位置
$PY_VERSION   = "3.11.9"   # 3.11 最後一個有 Windows 安裝檔的版本；鎖定清單就是在 3.11 上驗證的
$PY_URL       = "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-embed-amd64.zip"
$GETPIP_URL   = "https://bootstrap.pypa.io/get-pip.py"
# FFmpeg：先用固定版本（每台店裡的電腦都一樣），失敗再用 gyan.dev 的最新版；essentials 沒有 rubberband 就改用 full
$FFMPEG_URLS  = @(
  "https://github.com/GyanD/codexffmpeg/releases/download/9.0.1/ffmpeg-9.0.1-essentials_build.zip",
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.zip"
)
$VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
$WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$DEFAULT_ROOT = "C:\denki-karaoke"

# 測試用：換掉下載位置（本機假伺服器）
if ($env:DENKI_TEST_PY_URL)     { $PY_URL = $env:DENKI_TEST_PY_URL }
if ($env:DENKI_TEST_GETPIP_URL) { $GETPIP_URL = $env:DENKI_TEST_GETPIP_URL }
if ($env:DENKI_TEST_FFMPEG_URL) { $FFMPEG_URLS = @($env:DENKI_TEST_FFMPEG_URL) }

$SRC_APP = Split-Path -Parent $PSScriptRoot   # 這份安裝包裡的 app 資料夾
$IsWin = ($env:OS -eq "Windows_NT")

# ------------------------------------------------------------------ 顯示
function Title($text) { Write-Host ""; Write-Host $text -ForegroundColor White }
function Info($text)  { Write-Host "  $text" }
function Good($text)  { Write-Host "  $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "  $text" -ForegroundColor Yellow }
function Bad($text)   { Write-Host "  $text" -ForegroundColor Red }
function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }

function Fmt-Size([double]$bytes) {
  if ($bytes -ge 1GB) { return ("{0:0.0} GB" -f ($bytes / 1GB)) }
  if ($bytes -ge 1MB) { return ("{0:0} MB" -f ($bytes / 1MB)) }
  return ("{0:0} KB" -f [math]::Max(1, $bytes / 1KB))
}

function Show-Bar([double]$done, [double]$total) {
  $width = 30
  if ($total -gt 0) {
    $pct = [math]::Min(1.0, $done / $total)
    $fill = [int]([math]::Floor($pct * $width))
    $bar = ([string][char]0x2588) * $fill + ([string][char]0x2591) * ($width - $fill)
    $line = "  $bar {0,3}%  {1} / {2}" -f [int]($pct * 100), (Fmt-Size $done), (Fmt-Size $total)
  } else {
    $line = "  已下載 {0}" -f (Fmt-Size $done)
  }
  Write-Host -NoNewline ("`r" + $line.PadRight(64))
}

# ------------------------------------------------------------------ 下載（有進度、失敗重試）
function Get-Download([string]$url, [string]$dest) {
  Add-Type -AssemblyName System.Net.Http
  $dir = Split-Path -Parent $dest
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $last = $null
  for ($try = 1; $try -le 3; $try++) {
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(30)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd("DENKI-karaoke-installer/1.0")
    try {
      $resp = $client.GetAsync($url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
      if (-not $resp.IsSuccessStatusCode) { throw "伺服器回應 $([int]$resp.StatusCode)" }
      $total = 0
      if ($resp.Content.Headers.ContentLength) { $total = [double]$resp.Content.Headers.ContentLength }
      $in = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
      $out = [System.IO.File]::Create("$dest.part")
      try {
        $buf = New-Object byte[] (256KB)
        $done = 0; $tick = [Environment]::TickCount
        while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
          $out.Write($buf, 0, $n)
          $done += $n
          if ([Environment]::TickCount - $tick -gt 250) { Show-Bar $done $total; $tick = [Environment]::TickCount }
        }
        Show-Bar $done $total
        Write-Host ""
      } finally { $out.Close(); $in.Close() }
      if ($total -gt 0 -and $done -ne $total) { throw "下載不完整（$done / $total）" }
      Move-Item -Force "$dest.part" $dest
      return
    } catch {
      $last = $_
      Write-Host ""
      Warn "下載失敗（第 $try 次）：$($_.Exception.Message)"
      Start-Sleep -Seconds (2 * $try)
    } finally { $client.Dispose() }
  }
  throw "無法下載 $url：$last"
}

function Expand-Zip([string]$zip, [string]$dest) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)
}

# 執行外部程式：直接用這個視窗顯示（pip 的下載進度條才會出現），回傳結束代碼
# （不用 & 呼叫：函式裡 & 的輸出會變成回傳值，而且 pip 發現輸出被接走就不顯示進度條）
function Quote-Arg([string]$a) {
  if ($a -eq "") { return '""' }
  if ($a -match '[\s"]') { return '"' + ($a -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
  return $a
}
function Invoke-Exe([string]$exe, [string[]]$argv) {
  $line = ($argv | ForEach-Object { Quote-Arg $_ }) -join " "
  $p = Start-Process -FilePath $exe -ArgumentList $line -NoNewWindow -PassThru
  $null = $p.Handle          # 先拿住 handle，結束後才讀得到結束代碼
  $p.WaitForExit()
  return $p.ExitCode
}

# ------------------------------------------------------------------ 檢查電腦
function Get-NvidiaName {
  if (-not $IsWin) { return $null }
  try {
    $v = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
    if ($v) { return $v.Name }
  } catch {}
  return $null
}

function Get-FreeBytes([string]$path) {
  try {
    $drive = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($path))
    return (New-Object System.IO.DriveInfo($drive)).AvailableFreeSpace
  } catch { return -1 }
}

function Test-Internet {
  try {
    Add-Type -AssemblyName System.Net.Http
    $c = New-Object System.Net.Http.HttpClient
    $c.Timeout = [TimeSpan]::FromSeconds(10)
    $r = $c.GetAsync("https://pypi.org/simple/pip/").GetAwaiter().GetResult()
    $c.Dispose()
    return $r.IsSuccessStatusCode
  } catch { return $false }
}

function Test-VCRuntime {
  if (-not $IsWin) { return $true }
  $sys = Join-Path $env:WINDIR "System32"
  return (Test-Path (Join-Path $sys "msvcp140.dll")) -and (Test-Path (Join-Path $sys "vcruntime140_1.dll"))
}

function Get-WebView2Version {
  if (-not $IsWin) { return "test" }
  $guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
  foreach ($k in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
                   "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
                   "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$guid")) {
    try {
      $pv = (Get-ItemProperty -Path $k -Name pv -ErrorAction Stop).pv
      if ($pv -and $pv -ne "0.0.0.0") { return $pv }
    } catch {}
  }
  return $null
}

# ------------------------------------------------------------------ 安裝位置
function Test-DenkiFolder([string]$p) {
  return (Test-Path (Join-Path $p "app\karaoke")) -or (Test-Path (Join-Path $p "python\python.exe")) -or
         (Test-Path (Join-Path $p "venv\Scripts\python.exe"))
}

# 選到磁碟根目錄、或裡面已經有別的東西的資料夾 → 在裡面建 denki-karaoke 子資料夾
function Resolve-Target([string]$picked) {
  $full = [System.IO.Path]::GetFullPath($picked).TrimEnd('\', '/')
  if ($full.Length -le 3) { return (Join-Path ($full + [System.IO.Path]::DirectorySeparatorChar) "denki-karaoke") }
  if (-not (Test-Path $full)) { return $full }
  if (Test-DenkiFolder $full) { return $full }
  $items = @(Get-ChildItem -Force -LiteralPath $full -ErrorAction SilentlyContinue)
  if ($items.Count -eq 0) { return $full }
  return (Join-Path $full "denki-karaoke")
}

function Select-InstallFolder {
  $made = $false
  if (-not (Test-Path $DEFAULT_ROOT)) { New-Item -ItemType Directory -Path $DEFAULT_ROOT | Out-Null; $made = $true }
  Add-Type -AssemblyName System.Windows.Forms
  $owner = New-Object System.Windows.Forms.Form
  $owner.TopMost = $true
  $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
  $dlg.Description = "選擇 DENKI 伴奏工具 的安裝位置`n預設放在 $DEFAULT_ROOT，直接按「確定」即可。"
  $dlg.SelectedPath = $DEFAULT_ROOT
  $dlg.ShowNewFolderButton = $true
  $res = $dlg.ShowDialog($owner)
  $owner.Dispose()
  if ($res -ne [System.Windows.Forms.DialogResult]::OK) {
    if ($made) { Remove-Item $DEFAULT_ROOT -ErrorAction SilentlyContinue }
    return $null
  }
  $picked = Resolve-Target $dlg.SelectedPath
  if ($made -and $picked -ne $DEFAULT_ROOT) { Remove-Item $DEFAULT_ROOT -ErrorAction SilentlyContinue }
  return $picked
}

function Test-BadLocation([string]$root) {
  foreach ($bad in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:WINDIR)) {
    if ($bad -and $root.StartsWith($bad, [StringComparison]::OrdinalIgnoreCase)) {
      return "「$bad」需要系統管理員權限，程式之後沒辦法自己更新。請選別的地方（例如 $DEFAULT_ROOT）。"
    }
  }
  if ($env:OneDrive -and $root.StartsWith($env:OneDrive, [StringComparison]::OrdinalIgnoreCase)) {
    return "OneDrive 資料夾會一直同步好幾 GB 的 AI 檔案，請選別的地方（例如 $DEFAULT_ROOT）。"
  }
  return $null
}

function Get-RunningApp([string]$root) {
  if (-not $IsWin) { return @() }
  return @(Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) })
}

# ------------------------------------------------------------------ 各步驟
function Install-Python([string]$root) {
  $pyDir = Join-Path $root "python"
  $py = Join-Path $pyDir "python.exe"
  $pth = Join-Path $pyDir "python311._pth"
  if ((Test-Path $py) -and (Test-Path (Join-Path $pyDir "Lib\site-packages\pip"))) {
    Good "已經裝好了（$pyDir）"
    return $py
  }
  Info "下載 Python $PY_VERSION（嵌入式，約 11 MB）…"
  $zip = Join-Path $root "_download\python-embed.zip"
  Get-Download $PY_URL $zip
  Expand-Zip $zip $pyDir
  # 嵌入式 Python 預設不載入套件：打開 import site，並把 ..\app 加進搜尋路徑（程式在那裡）
  $lines = @("python311.zip", ".", "Lib\site-packages", "..\app", "import site")
  [System.IO.File]::WriteAllLines($pth, $lines)
  New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "Lib\site-packages") | Out-Null
  Info "安裝 pip…"
  $gp = Join-Path $root "_download\get-pip.py"
  Get-Download $GETPIP_URL $gp
  $code = Invoke-Exe $py @($gp, "--no-warn-script-location", "--disable-pip-version-check", "-q")
  if ($code -ne 0) { throw "pip 安裝失敗（代碼 $code）" }
  Good "完成"
  return $py
}

function Test-FFmpeg([string]$exe) {
  if (-not (Test-Path $exe)) { return $false }
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { $f = & $exe -hide_banner -filters 2>&1 | Out-String } finally { $ErrorActionPreference = $old }
  return ($f -match "rubberband")
}

function Install-FFmpeg([string]$root) {
  $bin = Join-Path $root "ffmpeg\bin"
  $exe = Join-Path $bin "ffmpeg.exe"
  if (Test-FFmpeg $exe) { Good "已經裝好了（含 rubberband 變調濾鏡）"; return }
  $zip = Join-Path $root "_download\ffmpeg.zip"
  $tmp = Join-Path $root "_download\ffmpeg"
  foreach ($url in $FFMPEG_URLS) {
    try {
      Info "下載 FFmpeg（約 100 MB）…"
      Get-Download $url $zip
      Expand-Zip $zip $tmp
      $found = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
      if (-not $found) { throw "壓縮檔裡找不到 ffmpeg.exe" }
      New-Item -ItemType Directory -Force -Path $bin | Out-Null
      Get-ChildItem -Path $found.DirectoryName -Filter "*.exe" | Copy-Item -Destination $bin -Force
      Remove-Item -Recurse -Force $tmp, $zip -ErrorAction SilentlyContinue
      if (Test-FFmpeg $exe) { Good "完成（含 rubberband 變調濾鏡）"; return }
      Warn "這個版本沒有 rubberband 變調濾鏡，換另一個來源…"
    } catch {
      Warn "這個來源失敗：$($_.Exception.Message)"
    }
  }
  if (-not $IsWin -and (Test-Path $exe)) { Good "完成（測試模式）"; return }
  throw "FFmpeg 安裝失敗"
}

function Copy-App([string]$root) {
  $dst = Join-Path $root "app"
  $src = [System.IO.Path]::GetFullPath($SRC_APP)
  if ([System.IO.Path]::GetFullPath($dst).TrimEnd('\', '/') -eq $src.TrimEnd('\', '/')) {
    Good "程式已經在安裝位置（$dst）"
    return
  }
  if (Test-Path $dst) {
    # 舊版先挪到 _update\before-install，裝好之後才刪（萬一複製到一半失敗還找得回來）
    $bak = Join-Path $root "_update\before-install"
    if (Test-Path $bak) { Remove-Item -Recurse -Force $bak }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bak) | Out-Null
    Move-Item $dst $bak
  }
  Copy-Item -Recurse -Path $src -Destination $dst
  foreach ($junk in @(".git", "ui\node_modules")) {
    $p = Join-Path $dst $junk
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
  }
  # 從網路下載的壓縮檔解出來的檔案會被 Windows 標記「來自網際網路」，拿掉標記免得跳警告
  if ($IsWin) { Get-ChildItem -Path $dst -Recurse -File | Unblock-File -ErrorAction SilentlyContinue }
  $bak = Join-Path $root "_update\before-install"
  if (Test-Path $bak) { Remove-Item -Recurse -Force $bak -ErrorAction SilentlyContinue }
  Good "完成（$dst）"
}

function New-Shortcut([string]$lnk, [string]$target, [string]$arguments, [string]$workdir, [string]$icon, [string]$desc) {
  $dir = Split-Path -Parent $lnk
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $sh = New-Object -ComObject WScript.Shell
  $sc = $sh.CreateShortcut($lnk)
  $sc.TargetPath = $target
  $sc.Arguments = $arguments
  $sc.WorkingDirectory = $workdir
  if ($icon -and (Test-Path $icon)) { $sc.IconLocation = "$icon,0" }
  $sc.Description = $desc
  $sc.Save()
}

function Write-Uninstaller([string]$root) {
  $bat = Join-Path $root "解除安裝.bat"
  $text = @(
    "@echo off",
    "chcp 65001 >nul",
    "rem 先把解除安裝腳本複製到暫存資料夾再執行（它會刪掉這個資料夾，包括這個檔案）",
    "copy /y ""%~dp0app\installer\uninstall.ps1"" ""%TEMP%\denki-uninstall.ps1"" >nul",
    "powershell -NoProfile -ExecutionPolicy Bypass -File ""%TEMP%\denki-uninstall.ps1"" -Root ""%~dp0."" & exit /b"
  )
  [System.IO.File]::WriteAllText($bat, (($text -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
  return $bat
}

function Install-Shortcuts([string]$root, [string]$py) {
  $pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
  $app = Join-Path $root "app"
  $icon = Join-Path $app "installer\denki.ico"
  $uninst = Write-Uninstaller $root
  if (-not $IsWin) { Good "（測試模式：略過捷徑）"; return }
  $desktop = [Environment]::GetFolderPath("Desktop")
  $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "DENKI 伴奏工具"
  New-Shortcut (Join-Path $desktop "DENKI 伴奏工具.lnk") $pyw "-m karaoke.app" $app $icon "DENKI 伴奏工具"
  New-Shortcut (Join-Path $menu "DENKI 伴奏工具.lnk") $pyw "-m karaoke.app" $app $icon "DENKI 伴奏工具"
  New-Shortcut (Join-Path $menu "解除安裝 DENKI 伴奏工具.lnk") $uninst "" $root "" "解除安裝 DENKI 伴奏工具"
  Good "桌面與開始選單已有「DENKI 伴奏工具」"
}

# ------------------------------------------------------------------ 主程式
function Main {
  $version = "?"
  $initPy = Join-Path $SRC_APP "karaoke\__init__.py"
  if (Test-Path $initPy) {
    $m = Select-String -Path $initPy -Pattern '__version__ = "([^"]+)"' | Select-Object -First 1
    if ($m) { $version = $m.Matches[0].Groups[1].Value }
  }
  $bar = "=" * 50
  Write-Host $bar -ForegroundColor Cyan
  Write-Host "  DENKI 伴奏工具 安裝程式 v$version" -ForegroundColor White
  Write-Host $bar -ForegroundColor Cyan
  $sw = [Diagnostics.Stopwatch]::StartNew()

  # ---------- 檢查電腦
  if ($IsWin -and -not [Environment]::Is64BitOperatingSystem) { throw "這台電腦是 32 位元 Windows，AI 分離需要 64 位元。" }
  $os = "測試環境"
  if ($IsWin) { try { $os = (Get-CimInstance Win32_OperatingSystem).Caption } catch { $os = "Windows" } }
  Write-Host ("這台電腦：{0}" -f $os)
  $gpuName = Get-NvidiaName
  $gpu = [bool]$gpuName -and -not $Cpu
  if ($gpuName) {
    Write-Host "顯示卡：  " -NoNewline; Write-Host $gpuName -ForegroundColor Green
    if ($gpu) { Write-Host "  → 裝「顯卡版」，三種分離模式都能用" } else { Write-Host "  → 依指定裝「CPU 版」" }
  } else {
    Write-Host "顯示卡：  " -NoNewline; Write-Host "沒有 NVIDIA 顯卡" -ForegroundColor Yellow
    Write-Host "  → 裝「CPU 版」：標準模式可以用（比較慢），高品質／保留和聲需要顯卡"
  }
  if ($gpu) { $env:DENKI_FORCE_CPU = "" } else { $env:DENKI_FORCE_CPU = "1" }

  if (-not (Test-Internet)) { throw "連不上網路（要下載約 3 GB 的元件）。請確認網路後再點一次 安裝.bat。" }
  Write-Host "網路：    可以連線 " -NoNewline; Write-Host "✓" -ForegroundColor Green

  # 系統元件：VC++ 執行階段（PyTorch 需要）、WebView2（視窗介面需要；Windows 11 內建）
  if (-not (Test-VCRuntime)) {
    Warn "缺少 Microsoft Visual C++ 執行階段，下載安裝（約 25 MB，可能會跳出權限確認，請按「是」）…"
    $vc = Join-Path $env:TEMP "vc_redist.x64.exe"
    Get-Download $VCREDIST_URL $vc
    Start-Process -FilePath $vc -ArgumentList "/install", "/quiet", "/norestart" -Wait
    if (-not (Test-VCRuntime)) { throw "Visual C++ 執行階段安裝失敗，請手動安裝：$VCREDIST_URL" }
  }
  Write-Host "系統元件：VC++ 執行階段 " -NoNewline; Write-Host "✓" -ForegroundColor Green -NoNewline
  if (-not (Get-WebView2Version)) {
    Write-Host ""
    Warn "缺少 WebView2（視窗介面需要），下載安裝（約 2 MB）…"
    $wv = Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe"
    Get-Download $WEBVIEW2_URL $wv
    Start-Process -FilePath $wv -ArgumentList "/silent", "/install" -Wait
    if (-not (Get-WebView2Version)) { throw "WebView2 安裝失敗，請到微軟網站搜尋「WebView2 Runtime」手動安裝。" }
  }
  Write-Host "　WebView2 " -NoNewline; Write-Host "✓" -ForegroundColor Green

  # ---------- 安裝位置
  if ($Target) { $root = Resolve-Target $Target }
  elseif ($Quiet) { $root = $DEFAULT_ROOT }
  else {
    Write-Host ""
    Write-Host "請在跳出的視窗選擇安裝位置…" -ForegroundColor Yellow
    $root = Select-InstallFolder
    if (-not $root) { Write-Host "已取消安裝。"; return 2 }
  }
  $why = Test-BadLocation $root
  if ($why) { throw $why }
  New-Item -ItemType Directory -Force -Path $root | Out-Null
  $need = 3GB; if ($gpu) { $need = 7GB }
  $free = Get-FreeBytes $root
  Write-Host ""
  Write-Host "安裝位置：$root" -ForegroundColor White
  if ($free -ge 0) {
    $msg = "需要空間：約 {0}（目前剩 {1}）" -f (Fmt-Size $need), (Fmt-Size $free)
    if ($free -lt $need) { throw "$msg，空間不夠。請清出空間，或選別的磁碟。" }
    Write-Host "$msg " -NoNewline; Write-Host "✓" -ForegroundColor Green
  }
  $running = Get-RunningApp $root
  while ($running.Count -gt 0) {
    Warn "DENKI 伴奏工具正在執行，請先關掉它，再按 Enter 繼續…"
    if ($Quiet) { throw "伴奏工具正在執行" }
    [void](Read-Host)
    $running = Get-RunningApp $root
  }
  if (Test-Path (Join-Path $root "venv\Scripts\python.exe")) {
    Info "（這個資料夾裡有舊的 venv 環境，安裝完成後就用不到了；確認新版正常後可以刪掉 venv 資料夾，約 5 GB）"
  }

  try { Start-Transcript -Path (Join-Path $root "install-log.txt") -Append | Out-Null } catch {}
  $env:PYTHONIOENCODING = "utf-8"
  $env:PYTHONUTF8 = "1"
  $env:PIP_NO_CACHE_DIR = "1"                         # 不留 2.5 GB 的下載快取
  $env:PIP_LOG = Join-Path $root "install-pip-log.txt"  # pip 的完整紀錄（出問題時看）
  $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"

  Step 1 "Python 3.11（放在安裝資料夾內）"
  $py = Install-Python $root

  Step 2 "FFmpeg（影音處理）"
  Install-FFmpeg $root

  Step 3 "程式本體 v$version"
  Copy-App $root

  $torchText = "CPU 版（約 200 MB）"; if ($gpu) { $torchText = "顯卡版（約 2.5 GB）" }
  Step 4 "AI 核心 PyTorch $torchText"
  if ($gpu) { Info "這一步最久，請不要關掉視窗" }
  $torchArg = "--cpu"; if ($gpu) { $torchArg = "--gpu" }
  $app = Join-Path $root "app"
  Push-Location $app
  try {
    $code = Invoke-Exe $py @("-m", "karaoke.deps", "--torch", $torchArg)
    if ($code -ne 0) { throw "PyTorch 安裝失敗（網路中斷？再點一次 安裝.bat 會從這裡繼續）" }

    Step 5 "其他套件（照驗證過的版本清單）"
    $code = Invoke-Exe $py @("-m", "karaoke.deps", $torchArg)
    if ($code -ne 0) { throw "套件安裝失敗（網路中斷？再點一次 安裝.bat 會從這裡繼續）" }

    Step 6 "自我檢查、建立捷徑"
    Install-Shortcuts $root $py
    Info "自我檢查中（約 1 分鐘）…"
    $code = Invoke-Exe $py @("-m", "tests.selftest")
    $selftestOk = ($code -eq 0)
  } finally { Pop-Location }

  Remove-Item -Recurse -Force (Join-Path $root "_download") -ErrorAction SilentlyContinue
  $sw.Stop()
  $mins = [int][math]::Floor($sw.Elapsed.TotalMinutes); $secs = $sw.Elapsed.Seconds
  Write-Host ""
  Write-Host ("─" * 50)
  if ($selftestOk) {
    Write-Host ("✓ 安裝完成！（用時 {0} 分 {1} 秒）" -f $mins, $secs) -ForegroundColor Green
  } else {
    Write-Host ("△ 安裝完成，但自我檢查有項目沒通過（用時 {0} 分 {1} 秒）" -f $mins, $secs) -ForegroundColor Yellow
    Write-Host ("  請把 {0} 傳給 Claude" -f (Join-Path $root "結果.txt")) -ForegroundColor Yellow
  }
  Write-Host "  桌面與開始選單已有「DENKI 伴奏工具」"
  if ($gpu) { Write-Host "  第一次用「高品質」模式時會再下載 AI 模型（約 600 MB）" }
  Write-Host "  要移除：開始選單 →「解除安裝 DENKI 伴奏工具」"
  try { Stop-Transcript | Out-Null } catch {}
  if (-not $Quiet -and $IsWin) {
    Write-Host ""
    [void](Read-Host "按 Enter 開啟伴奏工具")
    $pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
    Start-Process -FilePath $pyw -ArgumentList "-m", "karaoke.app" -WorkingDirectory $app
  }
  if ($selftestOk) { return 0 } else { return 3 }
}

if ($env:DENKI_INSTALL_DOTSOURCE -eq "1") { return }   # 測試：只載入函式
$exit = 1
try {
  $out = @(Main)
  $exit = [int]$out[-1]
} catch {
  Write-Host ""
  Bad ("✗ 安裝沒有完成：" + $_.Exception.Message)
  Write-Host "  修正後再點一次「安裝.bat」，已經裝好的部分會自動跳過。" -ForegroundColor Yellow
  Write-Host "  如果還是不行，把這個視窗截圖給 Claude（完整紀錄在安裝資料夾的 install-log.txt）。" -ForegroundColor Yellow
  try { Stop-Transcript | Out-Null } catch {}
  if (-not $Quiet) { [void](Read-Host "按 Enter 關閉") }
}
exit $exit
