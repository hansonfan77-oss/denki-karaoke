"""程式內更新：只收 GitHub 上「正式發佈」（Release，非草稿、非預先發佈）的版本。

流程（介面上的三個步驟）：
  1. 下載新版程式：GitHub 的 releases/latest → 版本標籤 vX.Y.Z、說明文字就是「這次改了什麼」，
     下載該版本的原始碼壓縮檔（程式本體不到 1 MB，介面已經建好放在 ui/dist），解壓到 _update\\new，
     並核對裡面的版本號真的是這一版、必要檔案都在。
  2. 補裝新套件：要等伴奏工具關掉才能動（Windows 上使用中的套件檔案不能覆蓋），
     所以交給「更新小幫手」（update_helper.py，只用 Python 內建功能），它會開一個小視窗顯示進度。
  3. 重新開啟：小幫手把 app 換成新版、照新版的鎖定清單補裝套件、檢查能不能啟動，再重新開啟伴奏工具。
     任何一步失敗 → 自動換回舊版（舊版備份在 _update\\backup），下次開啟時告訴使用者原因。

測試用環境變數：
  DENKI_UPDATE_API       換掉 https://api.github.com（本機假伺服器）
  DENKI_UPDATE_ENABLE=1  開發資料夾（有 .git、或不是 <安裝資料夾>\\app）也允許更新
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import __version__, config

API = os.environ.get("DENKI_UPDATE_API", "https://api.github.com").rstrip("/")
RESULT_FILE = "result.json"
CHECK_TIMEOUT = 8


# ------------------------------------------------------------------ 版本號
def parse_version(s: str) -> tuple[int, ...]:
    """'v0.3.1' → (0, 3, 1)。遇到非數字就停（'0.3.1-beta' → (0, 3, 1)）。"""
    out = []
    for part in s.strip().lstrip("vV").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group()))
        if m.end() != len(part):
            break
    return tuple(out)


def is_newer(tag: str, current: str = __version__) -> bool:
    new, cur = parse_version(tag), parse_version(current)
    return bool(new) and new > cur


def display_version(v: str) -> str:
    return "v" + v.lstrip("vV")


# ------------------------------------------------------------------ 狀態
_lock = threading.Lock()
STATE: dict = {
    "state": "idle",        # idle / checking / downloading / installing / restarting / error
    "latest": None,         # {tag, version, notes, published, zipball}
    "checkedAt": None,      # time.time()
    "error": None,
    "bytes": 0, "total": None,
    "depsTodo": None,       # 新版要補裝／調整的套件（None = 還沒算）
    "torchChange": False,
}

# 程式要關掉時呼叫（app.py 設成「關閉視窗」；沒設的話直接結束程序）
shutdown_hook: Optional[Callable[[], None]] = None
# 是否有 AI 分析正在跑（server.py 設定）：跑的時候不更新
busy_check: Optional[Callable[[], bool]] = None


def _set(**kw) -> None:
    with _lock:
        STATE.update(kw)


def enabled() -> tuple[bool, str]:
    """這份程式能不能自己更新（開發資料夾不行，免得蓋掉還沒推上去的修改）。"""
    if os.environ.get("DENKI_UPDATE_ENABLE") == "1":
        return True, ""
    if (config.APP_DIR / ".git").exists():
        return False, "這是開發資料夾（有 .git），請用 git 更新"
    if config.APP_DIR.name != "app" or config.APP_DIR.parent != config.ROOT:
        return False, "不是安裝程式裝的資料夾結構，無法自動更新"
    return True, ""


def last_result() -> Optional[dict]:
    p = config.UPDATE_DIR / RESULT_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def ack_result() -> None:
    (config.UPDATE_DIR / RESULT_FILE).unlink(missing_ok=True)


def public() -> dict:
    ok, why = enabled()
    with _lock:
        s = dict(STATE)
    latest = s["latest"]
    return {
        "current": __version__,
        "state": s["state"],
        "available": bool(latest and is_newer(latest["tag"])),
        "latest": latest,
        "checkedAt": s["checkedAt"],
        "error": s["error"],
        "bytes": s["bytes"], "total": s["total"],
        "depsTodo": s["depsTodo"], "torchChange": s["torchChange"],
        "enabled": ok, "disabledReason": why,
        "lastResult": last_result(),
    }


# ------------------------------------------------------------------ 檢查
def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"DENKI-karaoke/{__version__}",
    })


def check() -> dict:
    """問 GitHub 最新的正式版本。連不上網、還沒有任何發佈都不算錯誤（安靜地當作沒有新版）。"""
    with _lock:
        if STATE["state"] in ("downloading", "installing", "restarting"):
            return dict(STATE)
        STATE["state"] = "checking"
    url = f"{API}/repos/{config.GITHUB_REPO}/releases/latest"
    try:
        with urllib.request.urlopen(_request(url), timeout=CHECK_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        latest = {
            "tag": data["tag_name"],
            "version": display_version(data["tag_name"]),
            "notes": (data.get("body") or "").strip(),
            "published": (data.get("published_at") or "")[:10],
            "zipball": data.get("zipball_url") or f"{API}/repos/{config.GITHUB_REPO}/zipball/{data['tag_name']}",
        }
        _set(state="idle", latest=latest, checkedAt=time.time(), error=None)
        logging.info("檢查更新：最新 %s（目前 %s）", latest["tag"], __version__)
    except urllib.error.HTTPError as e:
        if e.code == 404:       # repo 還沒有任何正式發佈
            _set(state="idle", latest=None, checkedAt=time.time(), error=None)
        else:
            _set(state="idle", checkedAt=time.time(), error=f"GitHub 回應 {e.code}")
            logging.warning("檢查更新失敗：HTTP %s", e.code)
    except Exception as e:  # noqa: BLE001 — 沒網路、DNS、逾時
        _set(state="idle", checkedAt=time.time(), error="連不上 GitHub（沒有網路？）")
        logging.info("檢查更新失敗：%s", e)
    return public()


def check_in_background() -> None:
    threading.Thread(target=check, daemon=True, name="update-check").start()


# ------------------------------------------------------------------ 下載與核對
def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_request(url), timeout=30) as r, open(dest, "wb") as f:
        total = r.headers.get("Content-Length")
        _set(bytes=0, total=int(total) if total and total.isdigit() else None)
        got = 0
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            _set(bytes=got)


def find_app_root(folder: Path) -> Path:
    """壓縮檔裡的程式資料夾（GitHub 的原始碼壓縮檔外面會多包一層 owner-repo-xxxxx）。"""
    for cand in [folder, *sorted(p for p in folder.iterdir() if p.is_dir())]:
        if (cand / "karaoke" / "__init__.py").is_file():
            return cand
    raise RuntimeError("下載的檔案裡找不到程式（karaoke 資料夾）")


def read_version(app_dir: Path) -> str:
    text = (app_dir / "karaoke" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise RuntimeError("新版程式裡讀不到版本號")
    return m.group(1)


def verify_new_app(app_dir: Path, tag: str) -> None:
    ver = read_version(app_dir)
    if parse_version(ver) != parse_version(tag):
        # 版本號對不上會變成「更新完還是說有新版」的無限循環，寧可不更新
        raise RuntimeError(f"新版程式的版本號是 {ver}，跟發佈的 {tag} 對不上（請通知 Claude 修正發佈）")
    for need in ("ui/dist/index.html", "requirements-lock.txt", "karaoke/app.py", "karaoke/deps.py"):
        if not (app_dir / need).is_file():
            raise RuntimeError(f"新版程式缺少 {need}")


def deps_diff(new_app: Path) -> tuple[list[str], bool]:
    """新版要補裝／調整哪些套件；PyTorch 版本有沒有變（變的話要下載約 2.5 GB）。"""
    from . import deps

    want, have = deps.read_lock(new_app), deps.installed_versions()
    todo = [f"{n} {v}" for n, v in want.items() if have.get(n) != v]
    m = re.search(r'TORCH_VERSION\s*=\s*"([^"]+)"', (new_app / "karaoke" / "deps.py").read_text(encoding="utf-8"))
    torch_change = bool(m and m.group(1) != deps.TORCH_VERSION)
    return todo, torch_change


# ------------------------------------------------------------------ 執行更新
def start() -> dict:
    ok, why = enabled()
    if not ok:
        raise RuntimeError(why)
    if busy_check and busy_check():
        raise RuntimeError("AI 分析還在進行，等它完成再更新")
    with _lock:
        latest = STATE["latest"]
        if STATE["state"] in ("downloading", "installing", "restarting"):
            return dict(STATE)
        if not latest or not is_newer(latest["tag"]):
            raise RuntimeError("目前已經是最新版")
        STATE.update(state="downloading", error=None, bytes=0, total=None, depsTodo=None, torchChange=False)
    threading.Thread(target=_run, args=(latest,), daemon=True, name="update").start()
    return public()


def _run(latest: dict) -> None:
    upd = config.UPDATE_DIR
    new_dir = upd / "new"
    try:
        upd.mkdir(parents=True, exist_ok=True)
        zip_path = upd / "download.zip"
        logging.info("更新：下載 %s", latest["zipball"])
        _download(latest["zipball"], zip_path)
        shutil.rmtree(new_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(new_dir)
        zip_path.unlink(missing_ok=True)
        app_new = find_app_root(new_dir)
        verify_new_app(app_new, latest["tag"])
        todo, torch_change = deps_diff(app_new)
        _set(state="installing", depsTodo=todo, torchChange=torch_change)
        logging.info("更新：新版核對完成，要補裝 %d 個套件%s", len(todo), "（含 PyTorch）" if torch_change else "")
        time.sleep(1.2)     # 讓介面看得到「補裝新套件」這一步的說明
        launch_helper(app_new, latest["tag"])
        _set(state="restarting")
        time.sleep(0.8)
        _shutdown()
    except Exception as e:  # noqa: BLE001
        logging.exception("更新失敗")
        shutil.rmtree(new_dir, ignore_errors=True)
        _set(state="error", error=str(e))


def launch_helper(app_new: Path, tag: str) -> subprocess.Popen:
    """把小幫手複製到 _update（app 資料夾等一下會被換掉），用獨立視窗執行。"""
    helper = config.UPDATE_DIR / "update_helper.py"
    shutil.copy2(Path(__file__).with_name("update_helper.py"), helper)
    from .proc import python_exe
    args = [
        python_exe(), str(helper),
        "--app", str(config.APP_DIR), "--new", str(app_new), "--update-dir", str(config.UPDATE_DIR),
        "--pid", str(os.getpid()), "--from", __version__, "--to", display_version(tag),
    ]
    kw: dict = {"cwd": str(config.UPDATE_DIR)}   # 不能待在 app 資料夾裡，不然它換不掉
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    logging.info("更新：啟動小幫手 %s", args)
    return subprocess.Popen(args, **kw)


def _shutdown() -> None:
    logging.info("更新：關閉伴奏工具，交給小幫手")
    if shutdown_hook:
        try:
            shutdown_hook()
            return
        except Exception:  # noqa: BLE001
            logging.exception("關閉視窗失敗，直接結束")
    os._exit(0)
