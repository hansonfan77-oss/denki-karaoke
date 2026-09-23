"""人聲分離。

分離器做成可換的（對應 config.MODES）：
- "demucs"  ：標準模式。呼叫 `python -m demucs`（同一個 venv），自動選 GPU/CPU。
- "roformer"：高品質／保留和聲模式。呼叫 audio-separator（UVR 的核心引擎），只適合有顯卡的電腦。
- "fake"    ：測試用。用高低通濾波把合成測試音拆成兩軌，雲端沒有 GPU 也能驗證整條流程。

所有分離器都先輸出 WAV，最後統一轉成 FLAC（無損、約省一半空間）。

進度回報：progress(0~100, 說明文字)。命令列不傳 progress 時，Demucs 自己的進度條直接印在視窗上；
介面傳了 progress 時，改成解析 Demucs 的輸出換算成百分比。
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import config, ffmpeg
from .hardware import detect_device

Log = Callable[[str], None]
Progress = Callable[[float, str], None]


class SeparationError(RuntimeError):
    pass


class Cancelled(SeparationError):
    pass


class CancelToken:
    """介面按「取消」時用：設定 cancelled 並結束正在跑的子程序。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.proc: Optional[subprocess.Popen] = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def _drop_partial_model(model: str, log: Log) -> None:
    """下載到一半中斷的模型檔（比預期小很多）刪掉，讓 audio-separator 重新下載。
    audio-separator 看到檔案存在就不會再下載，半截的檔案會讓模型載入失敗或算錯。"""
    size_mb = next((m.get("size_mb") for m in config.MODES.values() if m.get("model") == model), None)
    path = config.MODELS_DIR / model
    if size_mb and path.exists() and path.stat().st_size < size_mb * 1e6 * 0.9:
        log(f"  模型檔不完整（{path.stat().st_size / 1e6:.0f} MB，應該約 {size_mb} MB），重新下載")
        path.unlink(missing_ok=True)


def roformer_available() -> bool:
    return importlib.util.find_spec("audio_separator") is not None and importlib.util.find_spec("onnxruntime") is not None


# Demucs 分離進度長這樣：" 37%|███▋      | 58.5/157.95 [00:03<00:05, 17.3seconds/s]"
# 下載模型的進度有單位字母（80.2M/80.2M），用「數字/數字 [」區分開來
_SEP_RE = re.compile(r"(\d{1,3})%\|[^|]*\|\s*[\d.]+/[\d.]+\s*\[")


def expected_passes(model: str) -> int:
    """htdemucs_ft 是 4 個模型的組合，進度條會跑 4 次。"""
    return 4 if model.endswith("_ft") else 1


def _separate_demucs(src: Path, no_vocals: Path, vocals: Path, model: str, device: str,
                     log: Log, progress: Optional[Progress], cancel: Optional[CancelToken]) -> None:
    if not demucs_available():
        raise SeparationError("這台電腦還沒安裝 Demucs。請先執行 setup.ps1。")

    with tempfile.TemporaryDirectory(prefix="denki-sep-") as tmp:
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems=vocals", "-n", model, "-d", device,
            "-o", tmp, str(src),
        ]
        log(f"  執行 Demucs（模型 {model}，{device}）… 第一次使用會先下載模型")

        if progress is None:
            # 命令列：不攔截輸出，讓 Demucs 自己的進度條直接顯示
            proc = subprocess.run(cmd)
            code, tail = proc.returncode, "請看上方紅字。"
        else:
            code, tail, _ = _run_with_progress(cmd, model, progress, cancel)

        if cancel and cancel.cancelled:
            raise Cancelled("已取消")
        if code != 0:
            raise SeparationError(f"Demucs 執行失敗（代碼 {code}）。{tail}")

        found_nv = list(Path(tmp).rglob("no_vocals.wav"))
        found_v = list(Path(tmp).rglob("vocals.wav"))
        if not found_nv or not found_v:
            raise SeparationError("Demucs 跑完了，但找不到輸出檔。")
        no_vocals.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(found_nv[0]), no_vocals)
        shutil.move(str(found_v[0]), vocals)


def _run_with_progress(cmd: list[str], model: str, progress: Progress,
                       cancel: Optional[CancelToken]) -> tuple[int, str]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # pythonw 下不要跳出黑視窗
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=flags, env=env)
    if cancel:
        cancel.proc = proc

    passes_total = expected_passes(model)
    passes_done, last_pct = 0, -1
    t_start, t_dl_end = time.perf_counter(), None
    tail: list[str] = []
    buf = b""
    progress(0, "準備 AI 模型…")
    assert proc.stderr is not None
    while True:
        chunk = proc.stderr.read(256)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            tail = (tail + [line])[-20:]
            if "Downloading" in line or re.search(r"[\d.]+[kMG]i?B?/[\d.]+[kMG]i?B?", line):
                m = re.search(r"(\d{1,3})%", line)
                pct = f" {m.group(1)}%" if m else ""
                progress(0, f"第一次使用這個模式，下載 AI 模型中…{pct}")
                t_dl_end = time.perf_counter()
                continue
            m = _SEP_RE.search(line)
            if not m:
                continue
            pct = int(m.group(1))
            if pct < last_pct:          # 進度條從頭開始 = 下一個模型
                passes_done += 1
            last_pct = pct
            overall = (min(passes_done, passes_total - 1) + pct / 100) / passes_total * 100
            progress(min(overall, 99.0), "AI 分離人聲與伴奏")
    code = proc.wait()
    dl = round(t_dl_end - t_start, 1) if t_dl_end is not None else 0.0
    return code, "\n".join(tail[-8:]), dl


def _separate_roformer(src: Path, no_vocals: Path, vocals: Path, model: str,
                       log: Log, progress: Optional[Progress], cancel: Optional[CancelToken]) -> None:
    if not roformer_available():
        raise SeparationError("這台電腦還沒安裝高品質分離元件（audio-separator）。請執行「更新並測試.bat」補裝。")
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    _drop_partial_model(model, log)

    with tempfile.TemporaryDirectory(prefix="denki-sep-") as tmp:
        cmd = [
            sys.executable, "-c", "import sys; from audio_separator.utils.cli import main; sys.exit(main())",
            str(src), "--model_filename", model, "--model_file_dir", str(config.MODELS_DIR),
            "--output_dir", tmp, "--output_format", "WAV",
        ]
        log(f"  執行 audio-separator（模型 {model}）… 第一次使用會先下載模型")
        code, tail, dl = _run_with_progress(cmd, model, progress or _log_progress(log), cancel)
        if dl:
            log(f"  模型下載用了 {dl} 秒（只有第一次）")

        if cancel and cancel.cancelled:
            raise Cancelled("已取消")
        if code != 0:
            raise SeparationError(f"audio-separator 執行失敗（代碼 {code}）。{tail}")

        inst = list(Path(tmp).rglob("*(Instrumental)*"))
        voc = list(Path(tmp).rglob("*(Vocals)*"))
        if not inst or not voc:
            raise SeparationError("audio-separator 跑完了，但找不到輸出檔。")
        no_vocals.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(inst[0]), no_vocals)
        shutil.move(str(voc[0]), vocals)
    return dl


def _log_progress(log: Log) -> Progress:
    """沒有進度條時（命令列、自動測試），進度訊息改成偶爾印一行。"""
    last = {"msg": None, "tick": -1}

    def cb(pct: float, msg: str) -> None:
        tick = int(pct // 25)
        if msg != last["msg"] or tick != last["tick"]:
            if "下載" in msg and last["msg"] and "下載" in last["msg"] and tick == last["tick"]:
                return
            last.update(msg=msg, tick=tick)
            log(f"    {msg}" if "%" in msg or "下載" in msg else f"    {msg} {pct:.0f}%")
    return cb


def _separate_fake(src: Path, no_vocals: Path, vocals: Path, log: Log,
                   progress: Optional[Progress], cancel: Optional[CancelToken]) -> None:
    """測試用：低頻當伴奏、高頻當人聲。只對合成測試音（220Hz + 440Hz）有意義。

    環境變數 DENKI_FAKE_DELAY=秒數 可以模擬分離需要時間（測試介面進度條用）。
    """
    log("  使用測試用分離器（fake）")
    delay = float(os.environ.get("DENKI_FAKE_DELAY", "0") or 0)
    steps = 20
    for i in range(steps):
        if cancel and cancel.cancelled:
            raise Cancelled("已取消")
        if progress:
            progress(i / steps * 100, "AI 分離人聲與伴奏")
        if delay:
            time.sleep(delay / steps)
    no_vocals.parent.mkdir(parents=True, exist_ok=True)
    lp = ",".join(["lowpass=f=290"] * 8)
    hp = ",".join(["highpass=f=370"] * 8)
    ffmpeg.run(["-i", src, "-af", lp, no_vocals])
    ffmpeg.run(["-i", src, "-af", hp, vocals])


def separate(
    src: Path,
    no_vocals: Path,
    vocals: Path,
    *,
    backend: str = "demucs",
    model: str = config.DEFAULT_MODEL,
    device: str | None = None,
    log: Log = print,
    progress: Optional[Progress] = None,
    cancel: Optional[CancelToken] = None,
) -> dict:
    """把 src 拆成伴奏與人聲。回傳 {'backend','model','device','seconds','download_seconds'}。

    no_vocals / vocals 的副檔名決定最後存成什麼格式（.flac 會自動轉檔）。
    """
    t0 = time.perf_counter()
    dl = 0.0
    tmp_nv = no_vocals.with_name(no_vocals.stem + "_tmp.wav")
    tmp_v = vocals.with_name(vocals.stem + "_tmp.wav")
    try:
        if backend == "fake":
            _separate_fake(src, tmp_nv, tmp_v, log, progress, cancel)
            dev = "cpu"
        elif backend == "demucs":
            dev = device or detect_device()["device"]
            _separate_demucs(src, tmp_nv, tmp_v, model, dev, log, progress, cancel)
        elif backend == "roformer":
            dev = detect_device()["device"]  # audio-separator 自己偵測顯卡
            dl = _separate_roformer(src, tmp_nv, tmp_v, model, log, progress, cancel)
        else:
            raise SeparationError(f"不認得的分離器：{backend}")
        if progress:
            progress(99.0, "轉存中間檔…")
        for tmp, final in ((tmp_nv, no_vocals), (tmp_v, vocals)):
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.suffix.lower() == ".flac":
                ffmpeg.run(["-i", tmp, "-c:a", "flac", final])
                tmp.unlink(missing_ok=True)
            else:
                shutil.move(str(tmp), final)
    finally:
        tmp_nv.unlink(missing_ok=True)
        tmp_v.unlink(missing_ok=True)
    return {
        "backend": backend,
        "model": "fake" if backend == "fake" else model,
        "device": dev,
        "seconds": round(time.perf_counter() - t0 - dl, 1),   # 不含第一次下載模型的時間
        "download_seconds": dl,
    }
