"""輸出成最終檔案：影片（只換音軌）、MP3、WAV。

檔名規則：<歌名>[_高品質|_保留和聲]_伴奏_<原key|+3key|-3key>[_導唱20][_速度90][_預備拍].<副檔名>
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import config, ffmpeg
from .project import slugify

FORMATS = ("video", "mp3", "wav")


def key_label(key: int) -> str:
    return "原key" if key == 0 else f"{key:+d}key"


def output_name(title: str, key: int, guide: int, tempo: int, ext: str, tag: str = "",
                countin: bool = False) -> str:
    parts = [slugify(title) + tag, "伴奏", key_label(key)]
    if guide > 0:
        parts.append(f"導唱{guide}")
    if tempo != 100:
        parts.append(f"速度{tempo}")
    if countin:
        parts.append("預備拍")
    return "_".join(parts) + ext


def video_container_ext(source: Path) -> str:
    """mp4 系列維持 .mp4；其他（mkv、webm…）一律 .mkv，因為 webm 不能裝 AAC。"""
    return ".mp4" if source.suffix.lower() in {".mp4", ".mov", ".m4v"} else ".mkv"


def to_video(source_video: Path, audio_wav: Path, out: Path) -> Path:
    """畫面原封不動（stream copy，不重新編碼），只換音軌。幾秒完成、畫質零損失。"""
    args: list = [
        "-i", source_video, "-i", audio_wav,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", config.VIDEO_AUDIO_BITRATE,
        "-shortest",
    ]
    if out.suffix.lower() == ".mp4":
        args += ["-movflags", "+faststart"]
    args.append(out)
    ffmpeg.run(args)
    return out


def to_mp3(audio_wav: Path, out: Path) -> Path:
    ffmpeg.run(["-i", audio_wav, "-c:a", "libmp3lame", "-q:a", config.MP3_QUALITY, out])
    return out


def to_wav(audio_wav: Path, out: Path) -> Path:
    shutil.copyfile(audio_wav, out)
    return out
