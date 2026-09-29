# DENKI 伴奏工具 解除安裝（由安裝資料夾裡的「解除安裝.bat」複製到暫存資料夾後執行）
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [switch]$Yes,          # 不問問題（測試用）：刪程式、保留 songs
  [switch]$DeleteSongs   # 搭配 -Yes：連 songs 一起刪
)
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Root = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
$IsWin = ($env:OS -eq "Windows_NT")
Set-Location ([System.IO.Path]::GetTempPath())   # 不能待在要刪的資料夾裡

Write-Host ("=" * 50) -ForegroundColor Cyan
Write-Host "  解除安裝 DENKI 伴奏工具" -ForegroundColor White
Write-Host ("=" * 50) -ForegroundColor Cyan
Write-Host "安裝位置：$Root"

# 安全檢查：只刪看起來是伴奏工具的資料夾
if (-not (Test-Path (Join-Path $Root "app\karaoke")) -and -not (Test-Path (Join-Path $Root "python\python.exe"))) {
  Write-Host "這個資料夾看起來不是 DENKI 伴奏工具的安裝位置，不做任何動作。" -ForegroundColor Red
  if (-not $Yes) { [void](Read-Host "按 Enter 關閉") }
  exit 1
}

function Size-Of([string]$p) {
  if (-not (Test-Path $p)) { return 0 }
  $s = (Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Sum Length).Sum
  if ($s) { return [double]$s } else { return 0 }
}
function Fmt([double]$b) { if ($b -ge 1GB) { "{0:0.0} GB" -f ($b / 1GB) } else { "{0:0} MB" -f ($b / 1MB) } }

$songs = Join-Path $Root "songs"
$keepSongs = $true
if ($Yes) {
  $keepSongs = -not $DeleteSongs
} else {
  $ans = Read-Host "確定要解除安裝嗎？(Y/N)"
  if ($ans -notmatch '^[Yy]') { Write-Host "已取消。"; exit 0 }
  if (Test-Path $songs) {
    Write-Host ""
    Write-Host ("songs 資料夾（{0}）裡有處理過的歌，以及「沒有另外指定輸出資料夾」時輸出的伴奏檔。" -f (Fmt (Size-Of $songs)))
    $ans = Read-Host "要一起刪除嗎？直接按 Enter = 保留"
    $keepSongs = -not ($ans -match '^[Yy]')
  }
}

# 伴奏工具還開著就先關掉
if ($IsWin) {
  Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Write-Host "關閉執行中的伴奏工具（$($_.Id)）…"; Stop-Process -Id $_.Id -Force }
  Start-Sleep -Seconds 1
}

# 捷徑
if ($IsWin) {
  $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "DENKI 伴奏工具.lnk"
  if (Test-Path $lnk) { Remove-Item -Force $lnk }
  $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "DENKI 伴奏工具"
  if (Test-Path $menu) { Remove-Item -Recurse -Force $menu }
  Write-Host "已移除桌面與開始選單的捷徑"
}

# 程式與資料（songs 看使用者選擇）
$failed = @()
foreach ($item in Get-ChildItem -LiteralPath $Root -Force) {
  if ($keepSongs -and $item.Name -eq "songs") { continue }
  try {
    Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction Stop
  } catch {
    $failed += $item.Name
  }
}
if ($keepSongs -and (Test-Path $songs)) {
  Write-Host "已保留：$songs" -ForegroundColor Yellow
} else {
  try { Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction Stop } catch { $failed += $Root }
}

Write-Host ""
if ($failed.Count -gt 0) {
  Write-Host ("有些檔案刪不掉（可能正在使用）：{0}" -f ($failed -join "、")) -ForegroundColor Yellow
  Write-Host "重新開機後手動刪除 $Root 即可。"
} else {
  Write-Host "✓ 解除安裝完成" -ForegroundColor Green
}
if (-not $Yes) { [void](Read-Host "按 Enter 關閉") }
exit 0
