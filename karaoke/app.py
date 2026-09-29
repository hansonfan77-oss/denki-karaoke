"""桌面程式入口：啟動本機後端 + 開一個 Windows 視窗（pywebview，內核是 Edge WebView2）。

  pythonw -m karaoke.app          正式使用（沒有黑色主控台視窗）
  python  -m karaoke.app --debug  診斷：留著主控台、可以按 F12 開開發者工具

介面拿不到拖進來的檔案完整路徑時會改走上傳；「選擇檔案」按鈕則走這裡的原生對話框。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from pathlib import Path

from . import config

LOG_PATH = config.ROOT / "app.log"


def _setup_logging() -> None:
    config.ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # pythonw 沒有 stdout/stderr，任何 print 都會出錯；導到 log
    if sys.stdout is None or sys.stderr is None:
        sink = open(LOG_PATH, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink


def _dialog(kind: str):
    """pywebview 6 起改用 FileDialog.OPEN / FOLDER；舊版用 OPEN_DIALOG / FOLDER_DIALOG。"""
    import webview

    fd = getattr(webview, "FileDialog", None)
    if fd is not None:
        return getattr(fd, kind)
    return getattr(webview, f"{kind}_DIALOG")


class Api:
    """前端透過 window.pywebview.api.xxx() 呼叫。

    ⚠️ 這個物件上只能有「方法」，其他東西一律用底線開頭（例如 self._window）。
    pywebview 每次載入頁面都會把這個物件的公開屬性一層層往下翻，產生給網頁用的函式清單；
    公開的 self.window 會讓它翻遍整個視窗物件（含 .NET 內部元件），翻好幾百層、佔住 GIL，
    視窗就「沒有回應」（2.3.2 的 freeze.log 抓到的真兇）。
    """

    def __init__(self) -> None:
        self._window = None

    def pick_file(self):
        exts = ";".join(f"*{e}" for e in sorted(config.SUPPORTED_EXTS))
        res = self._window.create_file_dialog(
            _dialog("OPEN"), allow_multiple=False,
            file_types=(f"音檔或影片 ({exts})", "所有檔案 (*.*)"),
        )
        return res[0] if res else None

    def pick_folder(self):
        res = self._window.create_file_dialog(_dialog("FOLDER"))
        return res[0] if res else None


def _hook_drop(window) -> None:
    """拖放檔案時，從 Python 端拿到完整路徑傳給介面。

    預設關閉：這個掛勾會在視窗內部註冊 DOM 事件，實測有讓視窗沒回應的風險，
    而它省下的只是「拖放時不用複製一份檔案」。要試的話設環境變數 DENKI_DROP_HOOK=1。
    沒有它的時候，介面會自動改用上傳，功能完全一樣。
    """
    if os.environ.get("DENKI_DROP_HOOK") != "1":
        return
    try:
        from webview.dom import DOMEventHandler
    except Exception:  # noqa: BLE001 — 舊版 pywebview 沒有這個功能
        return

    def on_drop(e):
        try:
            files = (e or {}).get("dataTransfer", {}).get("files", [])
            path = files[0].get("pywebviewFullPath") if files else None
            if path:
                window.evaluate_js(f"window.__denkiDroppedPath && window.__denkiDroppedPath({json.dumps(path)})")
        except Exception:  # noqa: BLE001
            logging.exception("drop")

    try:
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, False)
    except Exception:  # noqa: BLE001
        logging.exception("無法註冊拖放事件")


def _log_environment() -> None:
    """啟動時記下版本資訊，回報問題時看 app.log 就知道環境。"""
    import platform
    from importlib import metadata

    from . import __version__

    def ver(pkg: str) -> str:
        try:
            return metadata.version(pkg)
        except Exception:  # noqa: BLE001
            return "?"
    logging.info("DENKI 伴奏工具 %s｜Python %s｜%s｜pywebview %s｜pythonnet %s",
                 __version__, platform.python_version(), platform.platform(), ver("pywebview"), ver("pythonnet"))


def main() -> int:
    _setup_logging()
    _log_environment()
    # 工作目錄移出 app 資料夾：捷徑把它設在 app，視窗元件（WebView2）等子程序會跟著「待在」app 裡，
    # 伴奏工具關掉後它們還會多活幾秒，程式內更新就搬不動 app（v0.5.0 更新踩到 WinError 32）。
    try:
        os.chdir(config.ROOT)
    except OSError:
        pass
    debug = "--debug" in sys.argv
    try:
        import webview

        # 讓等待 GIL 的執行緒（例如視窗的主執行緒）更快輪到，拖動視窗時比較不會被其他執行緒拖慢
        sys.setswitchinterval(0.001)

        from .hardware import warm_up
        from .server import serve

        warm_up()   # 背景先查顯卡（子程序），開畫面時就不用等

        url, _server = serve()
        logging.info("後端啟動：%s", url)
        api = Api()
        window = webview.create_window(
            "DENKI 伴奏工具", url, js_api=api,
            width=1120, height=780, min_size=(960, 680),
        )
        api._window = window
        from . import updater
        updater.shutdown_hook = window.destroy   # 程式內更新：關掉視窗，交給更新小幫手
        from . import watchdog
        watchdog.start(config.ROOT / "freeze.log", "DENKI 伴奏工具")
        # 一定要在背景執行緒做，才不會擋到視窗
        window.events.loaded += lambda: threading.Thread(target=_hook_drop, args=(window,), daemon=True).start()
        webview.start(debug=debug)
        return 0
    except Exception:  # noqa: BLE001
        msg = traceback.format_exc()
        logging.error(msg)
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, f"啟動失敗，詳細錯誤在：\n{LOG_PATH}\n\n{msg[-600:]}", "DENKI 伴奏工具", 0x10)
        except Exception:  # noqa: BLE001
            print(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
