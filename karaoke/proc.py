"""子程序共用設定。

- NO_WINDOW：Windows 上呼叫 ffmpeg、python 等主控台程式時不要跳出黑色視窗
  （桌面程式用 pythonw 執行，沒有主控台，每個子程序都會各自開一個黑窗閃一下）。
- python_exe()：跑「背景 Python 子程序」用的直譯器。pythonw 沒辦法好好輸出文字，換成同資料夾的 python.exe。

為什麼要用子程序：PyTorch、librosa 這類大型套件「載入」的那幾秒會一直佔著 Python 的 GIL，
桌面視窗的主執行緒跟著卡住（Windows 顯示「沒有回應」）。放到子程序載入就完全不影響視窗。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NO_WINDOW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def python_exe() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)
