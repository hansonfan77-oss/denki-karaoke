"""預備拍：偵測速度（BPM）與第一拍、合成鼓棒聲、把預備拍接在音軌最前面。

時間都以「原曲時間軸」記錄（秒）；輸出時再依速度滑桿換算：
    週期 period = 60 / bpm
    變速後：period_out = period / (tempo/100)，first_out = first_beat / (tempo/100)
    前面要補的靜音 pad = max(0, 拍數 × period_out − first_out)
    第 k 下鼓棒（k = 拍數 … 1）落在 pad + first_out − k × period_out
這樣最後一下鼓棒之後剛好一拍，就是歌曲的第一拍。

偵測用 librosa（已隨高品質模式的套件安裝）。偵測不準時（清唱開頭、弱起、自由速度），
介面可以微調第一拍或點拍抓速度，結果存在 project.json 的 "beats"。
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

import numpy as np

from . import ffmpeg

BEATS_PER_BAR = 4
BPM_MIN, BPM_MAX = 50.0, 220.0
CLICK_SR = 44100
ANALYZE_SECONDS = 90          # 只看前 90 秒就夠抓速度與第一拍


class BeatError(RuntimeError):
    pass


def available() -> bool:
    """只查有沒有裝，不載入（librosa 載入要好幾秒、會佔住 GIL 讓視窗卡住）。"""
    import importlib.util
    return importlib.util.find_spec("librosa") is not None


def detect_isolated(path: Path) -> dict:
    """在子程序裡偵測（桌面程式用）：librosa 的載入與運算都不會拖慢視窗。"""
    import json
    import subprocess

    from .proc import NO_WINDOW, python_exe

    app_dir = Path(__file__).resolve().parents[1]
    proc = subprocess.run([python_exe(), "-m", "karaoke.beats", str(path)], cwd=app_dir,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, **NO_WINDOW)
    try:
        out = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        raise BeatError(f"偵測速度失敗：{(proc.stderr or '').strip()[-300:] or '沒有輸出'}") from e
    if "error" in out:
        raise BeatError(out["error"])
    return out


# ------------------------------------------------------------------ 偵測
def _music_start(y: np.ndarray, sr: int) -> float:
    """音樂從哪裡開始：第一個明顯有聲音的取樣點（前面的靜音不算）。用取樣點而不是分析框，誤差在 1 毫秒內。"""
    a = np.abs(y)
    ref = np.percentile(a, 99.5)
    if ref <= 1e-5:
        return 0.0
    idx = np.nonzero(a > ref * 0.12)[0]
    return float(idx[0] / sr) if len(idx) else 0.0


def detect(path: Path) -> dict:
    """回傳 {bpm, first_beat, music_start}（秒）。path 可以是任何 FFmpeg 讀得懂的音檔。"""
    try:
        import librosa
    except Exception as e:  # noqa: BLE001
        raise BeatError("這台電腦沒有 librosa，無法偵測速度。請執行「更新並測試.bat」補裝。") from e

    sr, hop = 22050, 256
    y = ffmpeg.decode_pcm(path, sr)[: sr * ANALYZE_SECONDS].astype(np.float32)
    if len(y) < sr * 2:
        raise BeatError("音檔太短，無法偵測速度。")

    start = _music_start(y, sr)
    # 只看 2 kHz 以下：大鼓、小鼓、貝斯定義「拍子」；高頻的腳踏鈸常打半拍，會讓速度被抓成兩倍
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, fmax=2000, n_mels=64)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, start_bpm=120)
    bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    if not bpm or not np.isfinite(bpm) or len(beats) < 4:
        raise BeatError("抓不到穩定的節拍（可能是清唱或速度很自由），請用「點拍抓速度」。")

    # 用整串拍點做直線擬合，速度比單一估計準（到小數點一位）
    if len(beats) >= 8:
        idx = np.arange(len(beats))
        slope, _ = np.polyfit(idx, beats, 1)
        if 0.5 * 60 / bpm < slope < 1.5 * 60 / bpm:
            bpm = 60.0 / slope
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm > BPM_MAX:
        bpm /= 2

    period = 60.0 / bpm
    beats = beats[beats >= start - period * 0.3]
    if len(beats):
        # 節拍追蹤常常慢幾拍才鎖定：沿著拍子格線往回推到音樂開頭
        b0 = float(beats[0])
        k = int(np.floor((b0 - start) / period + 0.3))
        first = b0 - k * period
        if first < start - period * 0.3:
            first += period
        # 離音樂開頭很近 → 就是開頭那一下（取樣點精度，避開分析框的延遲）
        if abs(first - start) < period * 0.25:
            first = start
    else:
        first = start
    return {"bpm": float(round(bpm, 2)), "first_beat": float(round(max(0.0, first), 3)), "music_start": float(round(start, 3))}


def from_taps(taps: list[float], *, near: float) -> dict:
    """點拍抓速度：taps 是點下去時的歌曲位置（秒）。回傳 {bpm, first_beat}。

    每一下對到第幾拍後做直線擬合（手抖一兩下不影響）；第一拍沿著點拍的格線，找最接近 near 的那一拍。
    """
    t = np.sort(np.asarray(taps, dtype=float))
    if len(t) < 4:
        raise BeatError("至少要點 4 下才算得出速度。")
    gaps = np.diff(t)
    gaps = gaps[(gaps > 60 / BPM_MAX * 0.8) & (gaps < 60 / BPM_MIN * 1.2)]
    if len(gaps) < 3:
        raise BeatError("點的間隔不穩定，請跟著音樂重點一次。")
    # 先用間隔中位數當初值，再把每一下對到第幾拍，做直線擬合：t = phase + 拍號 × period
    period = float(np.median(gaps))
    idx = np.round((t - t[0]) / period)
    period, phase = (float(v) for v in np.polyfit(idx, t, 1))
    k = round((near - phase) / period)
    first = phase + k * period
    if first < 0:
        first += period * np.ceil(-first / period)
    return {"bpm": float(round(60 / period, 2)), "first_beat": float(round(first, 3))}


# ------------------------------------------------------------------ 時間計算
def plan(bpm: float, first_beat: float, bars: int, tempo: int = 100) -> dict:
    """輸出時間軸上的預備拍配置：{pad, clicks[], period}（秒，已套用速度）。"""
    if bars not in (1, 2):
        raise BeatError("預備拍只能是 1 或 2 小節。")
    if not BPM_MIN <= bpm <= BPM_MAX:
        raise BeatError(f"速度要在 {BPM_MIN:.0f}～{BPM_MAX:.0f} BPM 之間（收到 {bpm}）")
    speed = tempo / 100
    period = 60.0 / bpm / speed
    first = max(0.0, first_beat) / speed
    n = bars * BEATS_PER_BAR
    pad = max(0.0, n * period - first)
    clicks = [pad + first - k * period for k in range(n, 0, -1)]
    return {"pad": round(pad, 4), "clicks": [round(c, 4) for c in clicks], "period": period}


# ------------------------------------------------------------------ 鼓棒聲
def stick_sample(sr: int = CLICK_SR) -> np.ndarray:
    """合成一下「鼓棒互敲」：短促的木頭敲擊（兩個共振峰 + 很短的噪音起音），約 60 毫秒。"""
    n = int(sr * 0.06)
    t = np.arange(n) / sr
    rng = np.random.default_rng(7)   # 固定種子：每次聲音一樣
    body = (np.sin(2 * np.pi * 2350 * t) * 0.55 + np.sin(2 * np.pi * 3900 * t) * 0.3) * np.exp(-t * 95)
    noise = rng.standard_normal(n) * np.exp(-t * 420) * 0.5
    # 簡單高通：去掉噪音裡的低頻，聽起來比較像木頭而不是鼓
    noise = np.concatenate([[0.0], np.diff(noise)])
    x = body + noise
    x[: int(sr * 0.0015)] *= np.linspace(0, 1, int(sr * 0.0015))
    return (x / np.max(np.abs(x))).astype(np.float32)


def write_wav(path: Path, mono: np.ndarray, sr: int = CLICK_SR, channels: int = 2) -> Path:
    data = np.clip(mono, -1, 1)
    pcm = (data * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def click_track(clicks: list[float], length: float, *, level: float = 0.6, sr: int = CLICK_SR) -> np.ndarray:
    """在指定時間點放鼓棒聲；第一下稍微重一點，聽得出小節開頭。"""
    out = np.zeros(int(sr * length) + 1, dtype=np.float32)
    s = stick_sample(sr)
    for i, c in enumerate(clicks):
        a = int(round(c * sr))
        amp = level * (1.0 if i % BEATS_PER_BAR == 0 else 0.8)
        seg = s[: max(0, min(len(s), len(out) - a))]
        out[a: a + len(seg)] += seg * amp
    return out


def add_count_in(src_wav: Path, out_wav: Path, *, bpm: float, first_beat: float, bars: int,
                 tempo: int = 100, tmp_dir: Optional[Path] = None) -> dict:
    """把預備拍接在 src_wav 最前面（src_wav 已經是變調／變速後的音軌）。回傳 plan。"""
    p = plan(bpm, first_beat, bars, tempo)
    tmp_dir = tmp_dir or out_wav.parent
    clicks_wav = tmp_dir / "_countin_clicks.wav"
    length = p["clicks"][-1] + 0.5
    write_wav(clicks_wav, click_track(p["clicks"], length))
    delay_ms = int(round(p["pad"] * 1000))
    try:
        ffmpeg.run([
            "-i", src_wav, "-i", clicks_wav,
            "-filter_complex",
            f"[0:a]adelay=delays={delay_ms}:all=1[m];"
            f"[m][1:a]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.97:level=false[out]",
            "-map", "[out]", "-ar", str(CLICK_SR), "-c:a", "pcm_s16le", out_wav,
        ])
    finally:
        clicks_wav.unlink(missing_ok=True)
    return p


if __name__ == "__main__":
    # 子程序入口：python -m karaoke.beats <音檔>  → 印出一行 JSON
    import json
    import sys
    try:
        print(json.dumps(detect(Path(sys.argv[1]))))
    except BeatError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"偵測速度失敗：{e}"}, ensure_ascii=False))
