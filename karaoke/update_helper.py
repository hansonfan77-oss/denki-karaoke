"""更新小幫手：伴奏工具關掉之後，把 app 換成新版、補裝套件、確認能啟動，再重新開啟。

⚠️ 只能用 Python 內建功能，不能 import karaoke 或任何第三方套件：
   它執行的時候 app 資料夾正在被換掉，套件也可能正在被 pip 更新。
由 updater.launch_helper() 複製到 _update\\update_helper.py 後執行（不在 app 資料夾裡跑）。

任何一步失敗 → 換回舊版（並照舊版的清單把套件裝回去），結果寫在 _update\\result.json，
伴奏工具下次開啟時會顯示。完整紀錄在 _update\\update.log。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LOG: Path | None = None


def say(msg: str = "") -> None:
    print(msg, flush=True)
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.now():%H:%M:%S} {msg}\n")


def wait_for_exit(pid: int, timeout: float = 60) -> bool:
    """等伴奏工具（pid）完全結束。"""
    end = time.time() + timeout
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return True     # 已經不在了
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout * 1000)) == 0
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def move(src: Path, dst: Path, tries: int = 40) -> None:
    """改名搬資料夾。檔案剛被關掉時 Windows 可能還鎖著一下下（防毒掃描也會），重試到 20 秒。"""
    last: Exception | None = None
    for _ in range(tries):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last = e
            time.sleep(0.5)
    raise RuntimeError(f"無法搬動 {src} → {dst}：{last}（是不是有檔案還開著？）")


def run(args: list[str], cwd: Path, env: dict) -> int:
    """執行並把輸出同時顯示在視窗、寫進紀錄。"""
    p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    assert p.stdout
    for line in p.stdout:
        say("    " + line.rstrip())
    return p.wait()


def pythonw(py: Path) -> Path:
    w = py.with_name("pythonw.exe")
    return w if w.exists() else py


def main() -> int:
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--update-dir", required=True)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--from", dest="old_ver", required=True)
    ap.add_argument("--to", dest="new_ver", required=True)
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    app, new, upd = Path(a.app), Path(a.new), Path(a.update_dir)
    backup, failed = upd / "backup", upd / "failed"
    LOG = upd / "update.log"
    py = Path(sys.executable)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if sys.platform == "win32":
        os.system(f"title DENKI 伴奏工具 — 更新到 {a.new_ver}")

    def result(ok: bool, message: str) -> None:
        (upd / "result.json").write_text(json.dumps({
            "ok": ok, "from": a.old_ver, "to": a.new_ver, "message": message,
            "at": _dt.datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False), encoding="utf-8")

    say("=" * 50)
    say(f"  DENKI 伴奏工具 更新：v{a.old_ver} → {a.new_ver}")
    say("  請不要關閉這個視窗，完成後會自動重新開啟")
    say("=" * 50)

    say("[1/4] 等伴奏工具關閉…")
    if not wait_for_exit(a.pid):
        say("      伴奏工具 60 秒內沒有關閉，取消更新")
        result(False, "伴奏工具沒有關閉，更新取消（舊版沒有變動）")
        shutil.rmtree(new, ignore_errors=True)
        return relaunch(app, py, 1)

    say("[2/4] 換成新版程式…")
    swapped = False
    try:
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(failed, ignore_errors=True)
        move(app, backup)
        try:
            move(new, app)
        except Exception:
            move(backup, app)   # 新版放不進去 → 舊版立刻放回原位
            raise
        swapped = True

        say("[3/4] 補裝新套件（照新版的鎖定清單，沒有變動的話幾秒就好）…")
        if run([str(py), "-m", "karaoke.deps"], app, env) != 0:
            raise RuntimeError("補裝套件失敗（網路斷了？）")

        say("[4/4] 檢查新版能不能啟動…")
        probe = "import karaoke.server, karaoke.app" + (", webview" if sys.platform == "win32" else "") + "; print('OK')"
        if run([str(py), "-c", probe], app, env) != 0:
            raise RuntimeError("新版程式無法載入")
    except Exception as e:  # noqa: BLE001
        say(f"✗ 更新失敗：{e}")
        if swapped:
            say("  換回舊版…")
            try:
                if app.exists():
                    move(app, failed)
                move(backup, app)
                say("  照舊版的清單把套件裝回去…")
                run([str(py), "-m", "karaoke.deps"], app, env)
            except Exception as e2:  # noqa: BLE001
                say(f"✗ 換回舊版也失敗：{e2}")
                say(f"  舊版備份在 {backup}，請截圖這個視窗給 Claude")
                result(False, f"更新失敗而且沒辦法自動換回舊版：{e2}。舊版備份在 {backup}")
                input("按 Enter 關閉…")
                return 1
        shutil.rmtree(new, ignore_errors=True)
        result(False, f"更新到 {a.new_ver} 失敗，已自動換回 v{a.old_ver}。原因：{e}")
        return relaunch(app, py, 1)

    shutil.rmtree(upd / "new", ignore_errors=True)
    result(True, f"已更新到 {a.new_ver}")
    say(f"✓ 更新完成：{a.new_ver}")
    return relaunch(app, py, 0)


def relaunch(app: Path, py: Path, code: int) -> int:
    if os.environ.get("DENKI_UPDATE_NO_RELAUNCH") == "1":
        return code
    say("重新開啟伴奏工具…")
    kw: dict = {"cwd": str(app)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([str(pythonw(py)), "-m", "karaoke.app"], **kw)
    time.sleep(1.5 if code == 0 else 4)
    return code


if __name__ == "__main__":
    sys.exit(main())
