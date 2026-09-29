"""FFmpeg / ffprobe 的薄包裝。

所有呼叫都用「參數陣列」，不拼字串 —— 檔名常有中日文、空白、驚嘆號
（例如「10 天使にふれたよ!.mp3」），拼字串一定會出事。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .proc import NO_WINDOW


class FFmpegError(RuntimeError):
    """FFmpeg 執行失敗。訊息裡帶最後幾行錯誤輸出，方便貼給 AI 看。"""


def _candidates(name: str) -> list[str]:
    from . import config

    out: list[str] = []
    # 安裝程式放在安裝資料夾裡的 FFmpeg 優先（可攜、版本固定、一定有 rubberband）
    for exe in (config.FFMPEG_DIR / f"{name}.exe", config.FFMPEG_DIR / name):
        if exe.is_file():
            out.append(str(exe))
    found = shutil.which(name)
    if found:
        out.append(found)
    # winget 安裝的 FFmpeg 在剛裝完、PATH 還沒刷新時，shutil.which 可能找不到
    local = os.environ.get("LOCALAPPDATA")
    if local:
        links = Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
        if links.exists():
            out.append(str(links))
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.exists():
            out.extend(str(p) for p in pkgs.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"))
    return out


@lru_cache(maxsize=None)
def find(name: str = "ffmpeg") -> str:
    for c in _candidates(name):
        if c:
            return c
    raise FFmpegError(f"找不到 {name}。請重新執行「安裝.bat」（會自動補裝 FFmpeg）。")


def run(args: list[str], *, quiet: bool = True) -> None:
    """執行 ffmpeg。args 不含開頭的 'ffmpeg'。"""
    cmd = [find("ffmpeg"), "-y", "-hide_banner"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise FFmpegError(f"FFmpeg 失敗（代碼 {proc.returncode}）：\n{tail}")


def probe(path: Path | str) -> dict:
    """回傳 {'duration': 秒, 'has_video': bool, 'has_audio': bool, 'video_codec': str|None}"""
    cmd = [
        find("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"無法讀取檔案：{path}\n{proc.stderr.strip()[-500:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    # 封面圖（mp3 內嵌專輯圖）也會被當成 video stream，要排除
    videos = [
        s for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return {
        "duration": duration,
        "has_video": bool(videos),
        "has_audio": bool(audios),
        "video_codec": videos[0].get("codec_name") if videos else None,
    }


@lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    proc = subprocess.run(
        [find("ffmpeg"), "-hide_banner", "-filters"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW,
    )
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines() if line.strip())


def decode_pcm(path: Path | str, sample_rate: int = 22050):
    """把音檔解碼成單聲道 float32 numpy 陣列（測試與之後畫波形用）。"""
    import numpy as np

    cmd = [
        find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, **NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"解碼失敗：{path}\n{proc.stderr.decode('utf-8', 'replace')[-500:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def loudness(path: Path | str) -> Optional[float]:
    """量整體響度（EBU R128 integrated loudness，單位 LUFS）。量不到回傳 None。"""
    cmd = [
        find("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
        "-af", "ebur128=framelog=quiet", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
    import re

    found = re.findall(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", proc.stderr or "")
    if not found or found[-1] == "-inf":
        return None
    return float(found[-1])
