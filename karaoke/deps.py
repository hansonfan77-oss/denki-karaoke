"""安裝／補裝套件：安裝程式、程式內更新、更新並測試.bat 共用同一套規則。

  python -m karaoke.deps            全部裝好（PyTorch + 鎖定清單），最後核對版本
  python -m karaoke.deps --torch    只裝 PyTorch
  python -m karaoke.deps --check    只核對版本，不安裝（有不符就回傳 1）
  加 --gpu / --cpu 可以強制顯卡版或 CPU 版（預設看有沒有 NVIDIA 顯卡）

規則（每一條都踩過）：
- 其他套件一律照 requirements-lock.txt 的「確切版本」、用 --no-deps 裝：
  不會因為某個套件出新版就裝到不一樣的東西（2.3.3 的 rotary-embedding-torch 0.9.1 讓高品質人聲全靜音）。
- 只裝現成的安裝檔（--only-binary）：店裡的電腦沒有編譯器。PyPI 上只有原始碼的 proxy-tools
  （pywebview 用的，純 Python）預先打包好放在 wheels\ 資料夾。
- PyTorch 從 download.pytorch.org 裝顯卡版（cu128）或 CPU 版；PyPI 上的 Windows 版是 CPU 版。
- 絕不裝 torchvision / onnx2torch-py313：torchvision 會把顯卡版 PyTorch 換成 CPU 版（2.1 踩過）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from . import config

Log = Callable[[str], None]

TORCH_VERSION = "2.11.0"
TORCHAUDIO_VERSION = "2.11.0"
TORCH_INDEX = {
    "cuda": "https://download.pytorch.org/whl/cu128",
    "cpu": "https://download.pytorch.org/whl/cpu",
}
NEVER_INSTALL = ["onnx2torch-py313", "torchvision"]
LOCK_FILE = "requirements-lock.txt"
WHEELS_DIR = "wheels"


def has_nvidia() -> bool:
    """有 NVIDIA 顯卡（驅動程式會附 nvidia-smi）。DENKI_FORCE_CPU=1 可以強制當作沒有。"""
    if os.environ.get("DENKI_FORCE_CPU") == "1":
        return False
    if shutil.which("nvidia-smi"):
        return True
    windir = os.environ.get("WINDIR", r"C:\Windows")
    return Path(windir, "System32", "nvidia-smi.exe").exists()


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(app_dir: Path | None = None) -> dict[str, str]:
    path = (app_dir or config.APP_DIR) / LOCK_FILE
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            out[_norm(name)] = ver.strip()
    return out


def installed_versions() -> dict[str, str]:
    from importlib import metadata
    out: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            out[_norm(name)] = dist.version
    return out


def _pip(args: list[str], log: Log, capture: bool) -> int:
    from .proc import NO_WINDOW
    cmd = [sys.executable, "-m", "pip", *args, "--disable-pip-version-check"]
    if capture:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
        if proc.returncode != 0:
            for line in (proc.stdout + proc.stderr).strip().splitlines()[-12:]:
                log("    " + line)
        return proc.returncode
    return subprocess.run(cmd).returncode


def torch_state() -> tuple[Optional[str], Optional[str]]:
    """（版本, 'cuda'|'cpu'）。沒裝回傳 (None, None)。只讀套件資訊，不載入 torch（載入要好幾秒）。"""
    v = installed_versions().get("torch")
    if not v:
        return None, None
    return v.split("+")[0], ("cpu" if "+cpu" in v or "+" not in v else "cuda")


def _skip_torch() -> bool:
    """只給雲端測試用：那邊連不到 download.pytorch.org。"""
    return os.environ.get("DENKI_DEPS_SKIP_TORCH") == "1"


def install_torch(gpu: Optional[bool] = None, *, log: Log = print, capture: bool = False) -> bool:
    if _skip_torch():
        log("  （測試模式：略過 PyTorch）")
        return True
    gpu = has_nvidia() if gpu is None else gpu
    want = "cuda" if gpu else "cpu"
    ver, build = torch_state()
    have_audio = installed_versions().get("torchaudio", "").split("+")[0]
    if ver == TORCH_VERSION and build == want and have_audio == TORCHAUDIO_VERSION:
        log(f"  PyTorch {TORCH_VERSION}（{'顯卡版' if gpu else 'CPU 版'}）已經裝好了")
        return True
    log(f"  安裝 PyTorch {TORCH_VERSION} {'顯卡版（約 2.5 GB，最久的一步）' if gpu else 'CPU 版（約 200 MB）'}…")
    args = ["install", "--no-deps", "--only-binary=:all:", f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}",
            "--index-url", TORCH_INDEX[want]]
    if ver and (ver != TORCH_VERSION or build != want):
        args.insert(1, "--force-reinstall")
    return _pip(args, log, capture) == 0


def install_locked(*, app_dir: Path | None = None, log: Log = print, capture: bool = False) -> bool:
    lock = (app_dir or config.APP_DIR) / LOCK_FILE
    want, have = read_lock(app_dir), installed_versions()
    todo = [f"{n}=={v}" for n, v in want.items() if have.get(n) != v]
    if not todo:
        log("  其他套件都已是鎖定的版本")
    else:
        log(f"  安裝／調整 {len(todo)} 個套件到鎖定的版本…")
        wheels = (app_dir or config.APP_DIR) / WHEELS_DIR
        args = ["install", "--no-deps", "--only-binary=:all:", "-r", str(lock)]
        if wheels.is_dir():
            args += ["--find-links", str(wheels)]
        if _pip(args, log, capture) != 0:
            return False
    stray = [n for n in NEVER_INSTALL if n in installed_versions()]
    if stray:
        log(f"  移除會搞壞 PyTorch 的套件：{', '.join(stray)}")
        _pip(["uninstall", "-y", *stray], log, True)
    return True


def check(app_dir: Path | None = None, gpu: Optional[bool] = None) -> list[str]:
    """核對版本，回傳不符合的項目（空清單＝全部正確）。"""
    gpu = has_nvidia() if gpu is None else gpu
    want, have = read_lock(app_dir), installed_versions()
    bad = [f"{n} 要 {v}，目前 {have.get(n, '沒有安裝')}" for n, v in want.items() if have.get(n) != v]
    ver, build = torch_state()
    if not _skip_torch() and (ver != TORCH_VERSION or build != ("cuda" if gpu else "cpu")):
        bad.append(f"torch 要 {TORCH_VERSION}（{'顯卡版' if gpu else 'CPU 版'}），目前 {ver or '沒有安裝'}（{build or '-'}）")
    bad += [f"{n} 不應該安裝（會搞壞 PyTorch）" for n in NEVER_INSTALL if n in have]
    return bad


def ensure(gpu: Optional[bool] = None, *, app_dir: Path | None = None, log: Log = print, capture: bool = False) -> bool:
    """全部裝好並核對。回傳是否成功。"""
    gpu = has_nvidia() if gpu is None else gpu
    ok = install_locked(app_dir=app_dir, log=log, capture=capture)
    ok = install_torch(gpu, log=log, capture=capture) and ok
    bad = check(app_dir, gpu)
    for b in bad:
        log(f"  ✗ {b}")
    if not bad:
        log(f"  ✓ {len(read_lock(app_dir)) + 2} 個套件版本全部正確")
    return ok and not bad


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog="python -m karaoke.deps")
    ap.add_argument("--torch", action="store_true", help="只安裝 PyTorch")
    ap.add_argument("--check", action="store_true", help="只核對版本")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--gpu", action="store_true")
    g.add_argument("--cpu", action="store_true")
    a = ap.parse_args()
    gpu = True if a.gpu else False if a.cpu else None
    if a.check:
        bad = check(gpu=gpu)
        print("\n".join(bad) if bad else "全部正確")
        return 1 if bad else 0
    if a.torch:
        return 0 if install_torch(gpu) else 1
    return 0 if ensure(gpu) else 1


if __name__ == "__main__":
    sys.exit(main())
