"""顯卡偵測。沒有 PyTorch 或沒有 NVIDIA 顯卡時一律回報 CPU 模式，不會報錯。

不在程式裡直接 import torch：載入 PyTorch 要好幾秒、期間佔住 GIL，會讓桌面視窗「沒有回應」
（2.3.2 的 freeze.log 抓到的）。改成用子程序查一次，結果記住，整個程式只查一次。
命令列或測試已經載入過 torch 的話，就直接在程式裡查（不多花時間）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

from .proc import NO_WINDOW, python_exe

_cache: dict | None = None
_lock = threading.Lock()

_PROBE = (
    "import json\n"
    "try:\n"
    "    import torch\n"
    "except Exception:\n"
    "    print(json.dumps({'torch': False})); raise SystemExit\n"
    "ok = False\n"
    "try:\n"
    "    ok = torch.cuda.is_available()\n"
    "except Exception:\n"
    "    pass\n"
    "print(json.dumps({'torch': True, 'cuda': ok, 'name': torch.cuda.get_device_name(0) if ok else ''}))\n"
)


def _result(info: dict) -> dict:
    if not info.get("torch"):
        return {"device": "cpu", "name": "CPU 模式（未安裝 PyTorch）", "torch": False}
    if info.get("cuda"):
        return {"device": "cuda", "name": info.get("name") or "NVIDIA 顯卡", "torch": True}
    return {"device": "cpu", "name": "CPU 模式", "torch": True}


def _probe() -> dict:
    if "torch" in sys.modules:                       # 已經載入了：直接查
        import torch
        try:
            ok = torch.cuda.is_available()
            return _result({"torch": True, "cuda": ok, "name": torch.cuda.get_device_name(0) if ok else ""})
        except Exception:  # noqa: BLE001
            return _result({"torch": True, "cuda": False})
    try:
        proc = subprocess.run([python_exe(), "-c", _PROBE], capture_output=True, text=True,
                              timeout=180, **NO_WINDOW)
        line = (proc.stdout or "").strip().splitlines()[-1]
        return _result(json.loads(line))
    except Exception:  # noqa: BLE001
        return _result({"torch": False})


def detect_device() -> dict:
    """回傳 {'device': 'cuda' | 'cpu', 'name': 顯示用名稱, 'torch': 是否有 PyTorch}（第一次約 2～5 秒，之後立即）"""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _probe()
        return dict(_cache)


def warm_up() -> None:
    """程式一啟動就在背景先查，第一次開畫面時就不用等。"""
    threading.Thread(target=detect_device, name="gpu-probe", daemon=True).start()
