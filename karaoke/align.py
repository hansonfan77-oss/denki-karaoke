"""AI 對時間（第 4 批）：貼上純文字歌詞 → 用這首歌分離出的「人聲」算出每一句的開始／結束。

用 stable-ts（Whisper 的「對齊」功能：已知歌詞，只找時間，不用辨識）。
- 模型（Whisper medium，約 1.5 GB）第一次用到才下載，放在 <安裝資料夾>\\models\\whisper\\。
  只用伴奏處理的電腦永遠不會下載。
- PyTorch 載入與運算放在子程序（karaoke.align_worker），視窗不會卡住；取消時直接結束子程序。
- 人聲優先用高品質模式的結果（最乾淨），其次保留和聲（只有主唱），最後標準。

環境變數（測試用）：
  DENKI_ALIGN_FAKE=1         不跑 AI：把句子平均分配在有人聲的時間裡（雲端測試整條流程）
  DENKI_ALIGN_MODEL_URL      換掉模型下載網址（本機假伺服器）
  DENKI_ALIGN_MODEL_SHA256   配合上面，假模型的 SHA-256
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import config, lyrics
from .project import Song

MODEL_NAME = "medium"
MODEL_SHA256 = "345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1"
MODEL_URL = f"https://openaipublic.azureedge.net/main/whisper/models/{MODEL_SHA256}/{MODEL_NAME}.pt"
MODEL_BYTES = 1_528_008_539        # 約 1.5 GB（下載進度的預估值，實際以伺服器回報為準）
VOCAL_ORDER = ("hq", "harmony", "standard")

Progress = Callable[[str, float, str], None]   # (stage, 0~100, 說明)


class AlignError(RuntimeError):
    pass


class Cancelled(AlignError):
    pass


def _fake() -> bool:
    return os.environ.get("DENKI_ALIGN_FAKE") == "1"


def model_dir() -> Path:
    return config.MODELS_DIR / "whisper"


def model_file() -> Path:
    return model_dir() / f"{MODEL_NAME}.pt"


def _ok_marker() -> Path:
    return model_file().with_suffix(".pt.ok")


def packages_ok() -> bool:
    if _fake():
        return True
    return importlib.util.find_spec("stable_whisper") is not None and importlib.util.find_spec("whisper") is not None


def model_ready() -> bool:
    if _fake() and not os.environ.get("DENKI_ALIGN_MODEL_URL"):
        return True
    return model_file().exists() and _ok_marker().exists()


def status() -> dict:
    return {
        "packages": packages_ok(),
        "model": model_ready(),
        "modelBytes": MODEL_BYTES,
        "modelName": MODEL_NAME,
    }


def vocals_for(song: Song) -> tuple[str, Path]:
    for mode in VOCAL_ORDER:
        if song.is_analyzed(mode):
            return mode, song.stem(mode, "vocals")
    raise AlignError("這首歌還沒分析（找不到人聲中間檔）。請先到「伴奏處理」分析一次，或中間檔被清除了要重新分析。")


# ------------------------------------------------------------------ 取消
class CancelToken:
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


# ------------------------------------------------------------------ 下載模型
def download_model(progress: Progress, cancel: Optional[CancelToken] = None) -> None:
    """下載 Whisper 模型並核對 SHA-256。中斷或核對失敗會刪掉半截的檔案，下次重新下載。"""
    url = os.environ.get("DENKI_ALIGN_MODEL_URL") or MODEL_URL
    want_sha = os.environ.get("DENKI_ALIGN_MODEL_SHA256") or MODEL_SHA256
    dest = model_file()
    part = dest.with_suffix(".pt.part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ok_marker().unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": lyrics.USER_AGENT})
    sha = hashlib.sha256()
    got, last = 0, 0.0
    try:
        with urllib.request.urlopen(req, timeout=30) as r, part.open("wb") as f:   # noqa: S310
            total = int(r.headers.get("Content-Length") or 0) or MODEL_BYTES
            while True:
                if cancel and cancel.cancelled:
                    raise Cancelled("已取消")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                now = time.monotonic()
                if now - last > 0.25:
                    last = now
                    progress("download", min(99.9, got / total * 100),
                             f"下載對時間模型 {got / 1e6:.0f} / {total / 1e6:.0f} MB")
    except Cancelled:
        part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        part.unlink(missing_ok=True)
        raise AlignError(f"下載對時間模型失敗（{e}）。請確認網路後再試一次。") from e
    if sha.hexdigest() != want_sha:
        part.unlink(missing_ok=True)
        raise AlignError("下載的模型檔不完整（核對不符），請再試一次。")
    part.replace(dest)
    _ok_marker().write_text(want_sha, encoding="utf-8")
    progress("download", 100.0, "模型下載完成")


# ------------------------------------------------------------------ 對時間
def _fake_words(vocals: Path, lines: list[str], clip: Optional[tuple[float, float]] = None) -> list[dict]:
    """測試用：找出有人聲的時間範圍，句子依字數比例平均分配（有 clip 時時間以片段開頭為 0）。"""
    from . import ffmpeg
    import numpy as np

    y = ffmpeg.decode_pcm(vocals, 8000)
    if clip:
        y = y[int(clip[0] * 8000):int(clip[1] * 8000)]
    dur = len(y) / 8000
    a = np.abs(y)
    ref = float(np.percentile(a, 99)) if len(a) else 0.0
    idx = np.nonzero(a > ref * 0.1)[0] if ref > 0 else np.array([])
    start = float(idx[0] / 8000) if len(idx) else 0.0
    end = float(idx[-1] / 8000) if len(idx) else dur
    delay = float(os.environ.get("DENKI_FAKE_DELAY", "0") or 0)
    if delay:
        time.sleep(delay)
    chars = [len(lyrics.norm_chars(ln)) or 1 for ln in lines]
    span = max(0.5, end - start) / sum(chars)
    words, t = [], start
    for ln, n in zip(lines, chars):
        words.append({"text": ln, "start": round(t, 3), "end": round(t + n * span * 0.9, 3)})
        t += n * span
    return words


def _run_worker(vocals: Path, text: str, lang: str, progress: Progress,
                cancel: Optional[CancelToken], clip: Optional[tuple[float, float]] = None) -> list[dict]:
    from .proc import NO_WINDOW, python_exe

    with tempfile.TemporaryDirectory(prefix="denki-align-") as tmp:
        job = Path(tmp) / "job.json"
        out = Path(tmp) / "out.json"
        job.write_text(json.dumps({
            "audio": str(vocals), "text": text, "lang": lang, "model": MODEL_NAME,
            "model_dir": str(model_dir()), "out": str(out),
            "clip": list(clip) if clip else None,
        }, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([python_exe(), "-m", "karaoke.align_worker", str(job)],
                                cwd=str(config.APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, **NO_WINDOW)
        if cancel:
            cancel.proc = proc
        err_tail: list[str] = []

        def read_err() -> None:
            assert proc.stderr is not None
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    err_tail.append(line)
                    del err_tail[:-15]

        t = threading.Thread(target=read_err, daemon=True)
        t.start()
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if msg.get("stage") == "load":
                progress("load", 0.0, f"載入 AI 模型（{msg.get('device', '')}）…")
            elif "progress" in msg:
                done, total = msg["progress"]
                progress("align", min(99.0, done / max(total, 1e-6) * 100), "AI 聽人聲對時間")
        code = proc.wait()
        t.join(timeout=2)
        if cancel and cancel.cancelled:
            raise Cancelled("已取消")
        if code != 0 or not out.exists():
            tail = "\n".join(err_tail[-6:])
            logging.error("對時間子程序失敗（%s）：%s", code, "\n".join(err_tail))
            raise AlignError(f"AI 對時間失敗（代碼 {code}）。{tail}")
        data = json.loads(out.read_text(encoding="utf-8"))
        if data.get("error"):
            raise AlignError(data["error"])
        return data.get("words", [])


def _align_lines(song: Song, lines: list[str], lang: str, progress: Progress,
                 cancel: Optional[CancelToken], clip: Optional[tuple[float, float]] = None) -> tuple[list[dict], str]:
    """（需要時）下載模型 → 子程序對時間 → 對應回每一句（含逐字時間）。回傳 (句子, 人聲來源模式)。"""
    mode, vocals = vocals_for(song)
    if not vocals.exists():
        raise AlignError("找不到人聲中間檔，請到「伴奏處理」重新分析這首歌。")
    if not packages_ok():
        raise AlignError("這台電腦還沒安裝 AI 對時間元件。請在程式右上角更新到最新版，或執行「更新並測試.bat」。")
    if not model_ready():
        download_model(progress, cancel)
    if _fake():
        progress("align", 50.0, "AI 聽人聲對時間")
        words = _fake_words(vocals, lines, clip)
    else:
        words = _run_worker(vocals, "\n".join(lines), lang, progress, cancel, clip)
    if cancel and cancel.cancelled:
        raise Cancelled("已取消")
    return lyrics.map_words_to_lines(words, lines), mode


def align_song(song: Song, text: str, *, lang: Optional[str] = None, progress: Optional[Progress] = None,
               cancel: Optional[CancelToken] = None) -> dict:
    """貼上的歌詞 → AI 對時間 → 存成這首歌的歌詞（含逐字時間）。回傳存好的歌詞。"""
    progress = progress or (lambda *_: None)
    lines = lyrics.split_plain(text)
    if not lines:
        raise AlignError("歌詞是空的：請貼上歌詞，一行一句。")
    if len(lines) > 400:
        raise AlignError("歌詞超過 400 行，看起來不像一首歌的歌詞。")
    lang = lang or lyrics.detect_language("\n".join(lines))
    t0 = time.perf_counter()
    mapped, mode = _align_lines(song, lines, lang, progress, cancel)
    progress("save", 100.0, "完成")
    logging.info("AI 對時間：%s，%d 句，%s，人聲來源 %s，%.1f 秒", song.slug, len(lines), lang, mode,
                 time.perf_counter() - t0)
    return lyrics.save(song, mapped, source="ai", lang=lang)


MAX_LINE_SPAN = 20.0   # AI 算出一句超過這麼長，多半是對錯了，這句不用逐字資料


CLIP_BEFORE = 1.0    # 單句精修：從這句開始前 1 秒
CLIP_AFTER = 1.0     # 到下一句開始後 1 秒（沒有下一句：這句開始後 MAX_LINE_SPAN 秒）


def _line_clip(lines: list[dict], i: int, offset: float) -> tuple[float, float]:
    """第 i 句在音檔裡的片段（秒）。歌詞時間＋整段位移＝音檔時間。"""
    t = lines[i]["t"] + offset
    nxt = lines[i + 1]["t"] + offset if i + 1 < len(lines) else t + MAX_LINE_SPAN
    end = min(nxt + CLIP_AFTER, t + MAX_LINE_SPAN + CLIP_AFTER)
    return max(0.0, t - CLIP_BEFORE), max(end, t + 2.0)


def refine_song(song: Song, *, line: Optional[int] = None, progress: Optional[Progress] = None,
                cancel: Optional[CancelToken] = None) -> dict:
    """用 AI 精修掃色：保留目前每一句的開始時間（使用者調好的），只補上每句裡每個字的時間。

    AI 對出來的逐字時間以「那一句在 AI 裡的開始」為基準，整句平移到使用者的開始時間；
    有逐字時間的句子，結束時間＝最後一個字的結束。
    line＝只精修這一句：只給 AI 聽這句附近的人聲，其他句完全不動。
    精修成功的句子會切成「逐字」（拿掉 even）。
    """
    progress = progress or (lambda *_: None)
    cur = lyrics.load(song)
    if not cur or not cur["lines"]:
        raise AlignError("這首歌還沒有歌詞。")
    texts = [ln["text"] for ln in cur["lines"]]
    lang = cur.get("lang") or lyrics.detect_language("\n".join(texts))
    t0 = time.perf_counter()
    if line is None:
        idx = list(range(len(texts)))
        mapped, mode = _align_lines(song, texts, lang, progress, cancel)
    else:
        if not 0 <= line < len(texts):
            raise AlignError("找不到這一句。")
        idx = [line]
        clip = _line_clip(cur["lines"], line, cur.get("offset") or 0.0)
        mapped, mode = _align_lines(song, [texts[line]], lang, progress, cancel, clip)
    out = [dict(ln) for ln in cur["lines"]]
    got = 0
    for i, ai in zip(idx, mapped):
        mine = out[i]
        words = ai.get("words")
        span = (ai.get("end") or ai["t"]) - ai["t"]
        if words and 0 < span <= MAX_LINE_SPAN:
            mine["words"] = lyrics.shift_words(words, mine["t"] - ai["t"])
            mine.pop("even", None)
            mine["end"] = mine["words"][-1]["end"]     # 結束＝最後一個字唱完（舊的結束可能被尾奏拉長）
            got += 1
        elif line is None:
            mine.pop("words", None)
            mine.pop("even", None)
    if line is not None and not got:
        raise AlignError("AI 這一句對不出來（可能這段人聲太小或歌詞跟歌不一樣），這句維持原樣。")
    progress("save", 100.0, "完成")
    logging.info("AI 精修掃色：%s，%s，%d/%d 句有逐字時間，人聲來源 %s，%.1f 秒", song.slug,
                 "整首" if line is None else f"第 {line + 1} 句", got, len(idx), mode, time.perf_counter() - t0)
    return lyrics.save(song, out, source=cur["source"], offset=cur["offset"], lang=lang, lrclib=cur.get("lrclib"))
