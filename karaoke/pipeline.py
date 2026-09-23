"""對外只有兩個動作：analyze（分析一次）與 render（輸出一個版本）。

介面層（命令列、桌面視窗）只呼叫這兩個函式。

分離模式（config.MODES）：同一首歌可以分析多種模式，結果並存；
render 時指定要用哪一種。工作音軌 source.wav 與原曲響度只量一次，各模式共用。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from . import beats, config, export, ffmpeg, mix, separate
from .hardware import detect_device
from .project import Song, now_iso, open_for_source

Log = Callable[[str], None]


class PipelineError(RuntimeError):
    pass


# ---------------------------------------------------------------- 模式
def mode_status(backend_override: Optional[str] = None) -> dict[str, dict]:
    """每個模式在這台電腦能不能用。{mode: {available, reason}}"""
    dev = detect_device()["device"]
    out = {}
    for m in config.MODE_ORDER:
        spec = config.MODES[m]
        if backend_override == "fake":
            ok, why = True, ""
        elif spec["backend"] == "demucs":
            ok = separate.demucs_available()
            why = "" if ok else "尚未安裝 Demucs"
        else:
            if not separate.roformer_available():
                ok, why = False, "尚未安裝高品質分離元件，請執行「更新並測試.bat」"
            elif spec.get("gpu_only") and dev != "cuda":
                ok, why = False, "需要 NVIDIA 顯卡（CPU 上一首歌要一個多小時）"
            else:
                ok, why = True, ""
        out[m] = {"available": ok, "reason": why}
    return out


def default_mode(backend_override: Optional[str] = None) -> str:
    """有顯卡又裝了高品質元件 → 高品質；否則標準。"""
    st = mode_status(backend_override)
    return "hq" if st["hq"]["available"] else "standard"


# ---------------------------------------------------------------- 分析
def analyze(
    source: Path | str,
    *,
    mode: Optional[str] = None,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    device: str | None = None,
    force: bool = False,
    songs_dir: Path | None = None,
    log: Log = print,
    progress: separate.Progress | None = None,
    cancel: separate.CancelToken | None = None,
) -> Song:
    """① 載入 → ② 分析（指定模式）。已分析過的模式直接回傳，不會重跑 AI（除非 force=True）。

    backend="fake" 時所有模式都用測試分離器（自動測試用）。
    """
    source = Path(source)
    if not source.exists():
        raise PipelineError(f"找不到檔案：{source}")
    if source.suffix.lower() not in config.SUPPORTED_EXTS:
        raise PipelineError(f"不支援的格式：{source.suffix}（支援：{', '.join(sorted(config.SUPPORTED_EXTS))}）")

    mode = mode or default_mode(backend)
    if mode not in config.MODES:
        raise PipelineError(f"不認得的分離模式：{mode}（可用：{', '.join(config.MODE_ORDER)}）")
    spec = config.MODES[mode]
    use_backend = backend or spec["backend"]
    use_model = model or spec["model"]

    song = open_for_source(source, songs_dir)
    if song.is_analyzed(mode) and not force:
        log(f"「{song.title}」的「{spec['label']}」模式已經分析過，直接使用保留的中間檔。")
        song.meta["last_mode"] = mode
        song.save()
        ensure_loudness(song, log=log)
        return song

    t0 = time.perf_counter()
    log(f"[1/3] 讀取檔案：{source.name}")
    if not song.work_wav.exists() or "loudness" not in song.meta:
        info = ffmpeg.probe(source)
        if not info["has_audio"]:
            raise PipelineError("這個檔案裡沒有聲音。")
        song.meta.update({
            "duration": round(info["duration"], 2),
            "has_video": info["has_video"],
            "video_codec": info["video_codec"],
        })
        log("      抽出音軌…")
        ffmpeg.run([
            "-i", source, "-vn", "-ac", str(config.WORK_CHANNELS),
            "-ar", str(config.WORK_SAMPLE_RATE), "-c:a", "pcm_s16le", song.work_wav,
        ])
        song.meta["loudness"] = ffmpeg.loudness(song.work_wav)
        song.save()

    log(f"[2/3] AI 分離人聲與伴奏（{spec['label']}）")
    nv, v = song.stem(mode, "no_vocals"), song.stem(mode, "vocals")
    # 一律存成 FLAC（舊的 wav 若存在會被新結果取代）
    nv, v = nv.with_suffix(config.STEM_EXT), v.with_suffix(config.STEM_EXT)
    sep = separate.separate(
        song.work_wav, nv, v,
        backend=use_backend, model=use_model, device=device, log=log,
        progress=progress, cancel=cancel,
    )
    log(f"      完成，用時 {sep['seconds']} 秒（{sep['device']}）")

    log("[3/3] 保存中間檔")
    song.modes[mode] = {
        "analyzed_at": now_iso(),
        "seconds": sep["seconds"], "device": sep["device"],
        "download_seconds": sep.get("download_seconds", 0.0),
        "model": sep["model"], "backend": sep["backend"],
        "nv_lufs": ffmpeg.loudness(nv),
        "v_lufs": ffmpeg.loudness(v),
        "rev": config.SEP_REV,
    }
    song.meta["last_mode"] = mode
    song.meta["analyze_seconds"] = round(time.perf_counter() - t0, 1)
    song.save()
    log(f"分析完成：{song.dir}")
    return song


def ensure_loudness(song: Song, log: Log = lambda *_: None) -> bool:
    """補量響度：2.1 版以前分析的歌沒有響度資料，音量補償會變成 0。第一次用到時補量，之後就存在 project.json。

    回傳是否有補量。
    """
    changed = False
    if song.meta.get("loudness") is None:
        src = song.work_wav if song.work_wav.exists() else Path(song.meta.get("source", ""))
        if src.is_file():
            log("補量原曲響度（舊版分析的歌只需要一次）…")
            song.meta["loudness"] = ffmpeg.loudness(src)
            changed = True
    for m in song.analyzed_modes():
        info = song.modes[m]
        if info.get("nv_lufs") is None:
            info["nv_lufs"] = ffmpeg.loudness(song.stem(m, "no_vocals"))
            changed = True
    if changed:
        song.save()
    return changed


# ---------------------------------------------------------------- 預備拍
def get_beats(song: Song, *, redetect: bool = False) -> dict:
    """速度與第一拍。第一次要用時才偵測（約幾秒），之後讀 project.json。

    回傳 {bpm, first_beat, music_start, manual, auto:{bpm, first_beat}}
    """
    b = song.meta.get("beats")
    if b and not redetect:
        return b
    mode = song.default_mode()
    src = song.stem(mode, "no_vocals") if mode else None      # 伴奏軌：沒有人聲干擾，鼓點最清楚
    if not src or not src.exists():
        src = song.work_wav if song.work_wav.exists() else song.source_path
    if not src.exists():
        raise PipelineError("找不到可以偵測速度的音檔，請重新拖入這首歌。")
    try:
        d = beats.detect_isolated(src)   # 子程序：不拖慢桌面視窗
    except beats.BeatError as e:
        raise PipelineError(str(e)) from e
    b = {**d, "manual": False, "auto": {"bpm": d["bpm"], "first_beat": d["first_beat"]}}
    song.meta["beats"] = b
    song.save()
    return b


def set_beats(song: Song, *, bpm: Optional[float] = None, first_beat: Optional[float] = None,
              taps: Optional[list[float]] = None) -> dict:
    """手動修正：直接給 bpm／first_beat，或給點拍的時間點（taps）重新算。"""
    b = dict(get_beats(song))
    if taps:
        try:
            r = beats.from_taps(taps, near=b["first_beat"])
        except beats.BeatError as e:
            raise PipelineError(str(e)) from e
        b.update(bpm=r["bpm"], first_beat=r["first_beat"])
    if bpm is not None:
        if not beats.BPM_MIN <= bpm <= beats.BPM_MAX:
            raise PipelineError(f"速度要在 {beats.BPM_MIN:.0f}～{beats.BPM_MAX:.0f} BPM 之間")
        b["bpm"] = round(float(bpm), 2)
    if first_beat is not None:
        b["first_beat"] = round(max(0.0, float(first_beat)), 3)
    b["manual"] = (b["bpm"], b["first_beat"]) != (b["auto"]["bpm"], b["auto"]["first_beat"])
    song.meta["beats"] = b
    song.save()
    return b


def reset_beats(song: Song) -> dict:
    """回到 AI 偵測的結果。"""
    b = dict(get_beats(song))
    b.update(bpm=b["auto"]["bpm"], first_beat=b["auto"]["first_beat"], manual=False)
    song.meta["beats"] = b
    song.save()
    return b


# ---------------------------------------------------------------- 輸出
def effective_compensation(song: Song, mode: str, guide: int, enabled: bool = True) -> float:
    """實際要補的 dB。導唱越多，人聲本身就補回了響度，所以按比例遞減（導唱 100% = 不補）。"""
    if not enabled:
        return 0.0
    return round(song.compensation_db(mode) * (1 - guide / 100), 2)


def render(
    song: Song,
    *,
    mode: Optional[str] = None,
    key: int = 0,
    guide: int = 0,
    fmt: str = "auto",
    also_mp3: bool = False,
    tempo: int = 100,
    compensate: bool = True,
    countin: int = 0,
    out_dir: Path | None = None,
    log: Log = print,
) -> list[Path]:
    """④ 輸出一個版本。回傳產生的檔案清單。

    fmt: "auto"（來源是影片就出影片，否則 mp3）/ "video" / "mp3" / "wav"
    countin: 預備拍小節數（0 = 不加，1 或 2）。只加在音檔（MP3／WAV）；
             影片維持原長度，但「同時輸出 MP3」的那個 MP3 會加。
    """
    mode = mode or song.default_mode()
    if not mode or not song.is_analyzed(mode):
        label = config.MODES.get(mode or "", {}).get("label", mode)
        raise PipelineError(f"「{song.title}」還沒用「{label}」模式分析，請先分析。")
    mix.validate(key, guide, tempo)
    if compensate:
        ensure_loudness(song, log=log)

    if fmt == "auto":
        fmt = "video" if song.has_video else "mp3"
    if fmt not in export.FORMATS:
        raise PipelineError(f"不認得的輸出格式：{fmt}（可用：video / mp3 / wav）")
    if fmt == "video" and not song.has_video:
        raise PipelineError("來源是音檔，沒有畫面可以輸出成影片。請改用 --format mp3 或 wav。")
    if fmt == "video" and tempo != 100:
        log("注意：影片輸出不能變速（畫面會對不上），這次速度維持 100%。")
        tempo = 100
    if countin not in (0, 1, 2):
        raise PipelineError("預備拍只能是 0、1 或 2 小節。")
    if countin and fmt == "video" and not also_mp3:
        log("注意：影片暫時不支援預備拍，這次不加（勾「同時輸出 MP3」或改選 MP3／WAV 就會加）。")
        countin = 0
    b = get_beats(song) if countin else None

    out_dir = Path(out_dir) if out_dir else song.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    gain = effective_compensation(song, mode, guide, compensate)
    tag = config.MODES[mode]["tag"]

    tmp_wav = song.dir / "_render_tmp.wav"
    log(f"混音：{config.MODES[mode]['label']}、Key {key:+d}、導唱 {guide}%、速度 {tempo}%、音量補償 {gain:+.1f} dB")
    mix.render_audio(song.stem(mode, "no_vocals"), song.stem(mode, "vocals"), tmp_wav,
                     key=key, guide=guide, tempo=tempo, gain_db=gain)

    outputs: list[Path] = []
    ci_wav = song.dir / "_render_countin.wav"
    try:
        audio_wav = tmp_wav
        if b:
            p = beats.add_count_in(tmp_wav, ci_wav, bpm=b["bpm"], first_beat=b["first_beat"],
                                   bars=countin, tempo=tempo, tmp_dir=song.dir)
            log(f"預備拍：{countin} 小節、♩{b['bpm']:g}，前面多 {p['pad']:.2f} 秒")
            audio_wav = ci_wav
        if fmt == "video":
            ext = export.video_container_ext(song.source_path)
            out = out_dir / export.output_name(song.title, key, guide, tempo, ext, tag)
            log("寫入影片（畫面不重新編碼）…")
            outputs.append(export.to_video(song.source_path, tmp_wav, out))   # 影片一律用原長度
        elif fmt == "wav":
            out = out_dir / export.output_name(song.title, key, guide, tempo, ".wav", tag, bool(b))
            outputs.append(export.to_wav(audio_wav, out))
        if fmt == "mp3" or also_mp3:
            out = out_dir / export.output_name(song.title, key, guide, tempo, ".mp3", tag, bool(b))
            outputs.append(export.to_mp3(audio_wav, out))
    finally:
        tmp_wav.unlink(missing_ok=True)
        ci_wav.unlink(missing_ok=True)

    secs = round(time.perf_counter() - t0, 1)
    song.meta["last_mode"] = mode
    for o in outputs:
        is_video = o.suffix.lower() in config.VIDEO_EXTS
        song.add_render({"mode": mode, "key": key, "guide": guide, "tempo": tempo,
                         "gain_db": gain, "countin": 0 if is_video else countin,
                         "format": o.suffix.lstrip("."), "file": str(o)})
    log(f"輸出完成，用時 {secs} 秒：")
    for o in outputs:
        log(f"  {o}")
    return outputs
