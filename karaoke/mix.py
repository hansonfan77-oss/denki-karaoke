"""混音：伴奏 + 導唱比例 → 變調 → 變速，一條 FFmpeg 指令完成。

關鍵細節（都踩過或查證過）：
- amix 一定要 normalize=0，否則兩軌相加時音量會被砍半。
- 變調用 rubberband（不變速），比例 = 2 ** (半音 / 12)，-3 → 0.8409、+5 → 1.3348。
- 音量補償（volume）放在 limiter 前面，補大聲了也不會破音。
- 最後加一個 limiter，避免變調或補償後峰值過高造成破音。
"""

from __future__ import annotations

from pathlib import Path

from . import config, ffmpeg


class MixError(ValueError):
    pass


def semitone_ratio(semitones: float) -> float:
    return 2 ** (semitones / 12)


def validate(key: int, guide: int, tempo: int) -> None:
    if not config.KEY_MIN <= key <= config.KEY_MAX:
        raise MixError(f"Key 要在 {config.KEY_MIN} 到 +{config.KEY_MAX} 之間（收到 {key}）")
    if not config.GUIDE_MIN <= guide <= config.GUIDE_MAX:
        raise MixError(f"導唱要在 0 到 100% 之間（收到 {guide}）")
    if not config.TEMPO_MIN <= tempo <= config.TEMPO_MAX:
        raise MixError(f"速度要在 {config.TEMPO_MIN} 到 {config.TEMPO_MAX}% 之間（收到 {tempo}）")


def build_filter(key: int, guide: int, tempo: int, gain_db: float = 0.0) -> tuple[str, bool]:
    """回傳 (filter_complex, 是否需要人聲軌當第二個輸入)。"""
    use_vocals = guide > 0
    chain = []
    if use_vocals:
        chain.append(f"[1:a]volume={guide / 100:.3f}[v]")
        chain.append("[0:a][v]amix=inputs=2:normalize=0:duration=longest[m]")
        last = "[m]"
    else:
        last = "[0:a]"

    post = []
    if key != 0 or tempo != 100:
        parts = ["pitchq=quality"]
        if key != 0:
            parts.insert(0, f"pitch={semitone_ratio(key):.6f}")
        if tempo != 100:
            parts.insert(0, f"tempo={tempo / 100:.4f}")
        post.append("rubberband=" + ":".join(parts))
    if gain_db:
        post.append(f"volume={gain_db:.2f}dB")   # 音量補償：把伴奏拉回原曲響度
    post.append("alimiter=limit=0.97:level=false")

    chain.append(f"{last}{','.join(post)}[out]")
    return ";".join(chain), use_vocals


def render_audio(no_vocals: Path, vocals: Path, out_wav: Path, *, key: int = 0, guide: int = 0,
                 tempo: int = 100, gain_db: float = 0.0) -> Path:
    """產生最終音軌（wav）。"""
    validate(key, guide, tempo)
    if (key != 0 or tempo != 100) and not ffmpeg.has_filter("rubberband"):
        raise MixError("這台電腦的 FFmpeg 沒有 rubberband 變調濾鏡。請用 winget install Gyan.FFmpeg 重裝。")

    fc, use_vocals = build_filter(key, guide, tempo, gain_db)
    args: list = ["-i", no_vocals]
    if use_vocals:
        args += ["-i", vocals]
    args += ["-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_s16le", out_wav]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(args)
    return out_wav
