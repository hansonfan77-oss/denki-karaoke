"""當機偵測：視窗「沒有回應」時，把每個執行緒當下在做什麼寫進 freeze.log。

做法跟 Windows 判斷「沒有回應」一樣：每 2 秒用 SendMessageTimeout 送一個空訊息（WM_NULL）給視窗，
視窗的訊息迴圈（也就是 pywebview 的主執行緒）有空就會立刻回。
- 用 ctypes 直接呼叫 Windows API：呼叫期間不佔 Python 的 GIL，偵測本身不會造成或加重卡頓
- 送出前先設定 faulthandler 計時器（在 C 層的獨立執行緒倒數，不需要 GIL）：
  視窗超過 DUMP_AFTER 秒沒回，就把所有執行緒的呼叫堆疊寫進 freeze.log，卡死也寫得出來
- 卡超過 1.5 秒但後來恢復的，也記一行到 app.log，方便看頻率

只在 Windows 啟用；其他系統什麼都不做。
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import time
from pathlib import Path

INTERVAL = 2.0
DUMP_AFTER = 6
GIVE_UP_MS = 30000

_file = None


def start(log_path: Path, window_title: str) -> None:
    if sys.platform != "win32":
        return
    global _file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _file = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — 要一直開著給 faulthandler 寫
    threading.Thread(target=_run, args=(window_title,), name="freeze-watchdog", daemon=True).start()


def _run(title: str) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    SMTO_ABORTIFHUNG = 0x0002
    WM_NULL = 0x0000

    hwnd = None
    for _ in range(120):                      # 等視窗建立（最多 1 分鐘）
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        logging.warning("當機偵測：找不到視窗「%s」，不啟用", title)
        return
    logging.info("當機偵測已啟用（視窗超過 %s 秒沒回應會寫 freeze.log）", DUMP_AFTER)

    result = ctypes.c_size_t()
    while True:
        time.sleep(INTERVAL)
        if not user32.IsWindow(hwnd):
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        faulthandler.dump_traceback_later(DUMP_AFTER, repeat=False, file=_file, exit=False)
        t0 = time.monotonic()
        # 注意：不能用 SMTO_ABORTIFHUNG 以外的旗標讓它「立刻放棄」，否則量不到卡多久
        ok = user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, GIVE_UP_MS, ctypes.byref(result))
        faulthandler.cancel_dump_traceback_later()
        waited = time.monotonic() - t0
        if waited > DUMP_AFTER:
            msg = f"視窗沒有回應約 {waited:.0f} 秒（{stamp} 開始；上方是卡住當下每個執行緒的狀態）"
            _file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
            logging.warning(msg)
        elif waited > 1.5:
            logging.warning("視窗短暫卡頓 %.1f 秒（%s）", waited, stamp)
        if not ok and waited < 1:
            # SMTO_ABORTIFHUNG：Windows 已經判定這個視窗「沒有回應」，會馬上回傳失敗
            _file.write(f"{stamp} Windows 判定視窗「沒有回應」\n")
            faulthandler.dump_traceback(file=_file, all_threads=True)
            time.sleep(DUMP_AFTER)            # 避免連續寫爆檔案
