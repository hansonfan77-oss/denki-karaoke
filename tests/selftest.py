"""自動測試。兩段：

A. 合成音測試（任何電腦都能跑，雲端也能跑）
   用 FFmpeg 產生一段「220Hz 當伴奏 + 440Hz 當人聲 + 測試畫面」的影片，
   用測試分離器拆開，再驗證：變調後頻率對不對、導唱有沒有混進去、
   影片畫面是不是原封不動、中日文檔名、第二次分析會不會重跑。

B. 真實歌曲測試（加 --real，在有 Demucs 的電腦上跑）
   拿 C:\\denki-karaoke 裡第一首歌真的跑一次 AI 分離 + 降 3 Key 輸出。

結果寫到 C:\\denki-karaoke\\結果.txt（或 --report 指定的位置）。

  python -m tests.selftest            只跑 A
  python -m tests.selftest --real     A + B
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np  # noqa: E402

from karaoke import __version__, beats, config, ffmpeg, pipeline, separate  # noqa: E402
from karaoke.hardware import detect_device  # noqa: E402
from karaoke.project import Song, find_song, list_songs  # noqa: E402

TEST_NAME = "10 天使にふれたよ! 測試"  # 故意用中日文 + 空白 + 驚嘆號
BASS_HZ, VOCAL_HZ = 220.0, 440.0
SR = 22050


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = 0
        self.passed = 0

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        self._add(f"✅ {name}" + (f"：{detail}" if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.failed += 1
        self._add(f"❌ {name}：{detail}")

    def info(self, text: str) -> None:
        self._add(text)

    def check(self, name: str, cond: bool, detail: str) -> None:
        (self.ok if cond else self.fail)(name, detail)

    def _add(self, line: str) -> None:
        print(line, flush=True)
        self.lines.append(line)


def peak_hz(samples: np.ndarray, lo: float = 100, hi: float = 1000) -> float:
    spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1 / SR)
    band = (freqs >= lo) & (freqs <= hi)
    return float(freqs[band][np.argmax(spec[band])])


def band_energy(samples: np.ndarray, center: float, width: float = 15) -> float:
    spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1 / SR)
    band = (freqs >= center - width) & (freqs <= center + width)
    return float(spec[band].sum() / (spec.sum() + 1e-9))


def middle(path: Path) -> np.ndarray:
    x = ffmpeg.decode_pcm(path, SR)
    n = len(x)
    return x[n // 4: 3 * n // 4]  # 避開頭尾的淡入淡出與濾波暫態


def make_test_video(path: Path, seconds: int = 6) -> None:
    ffmpeg.run([
        "-f", "lavfi", "-i", f"sine=f={BASS_HZ}:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f={VOCAL_HZ}:d={seconds}",
        "-f", "lavfi", "-i", f"testsrc=d={seconds}:s=320x240:r=25",
        "-filter_complex", "[0][1]amix=inputs=2:normalize=0[a]",
        "-map", "2:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path,
    ])


def video_stream(path: Path) -> dict:
    import json
    import subprocess

    out = subprocess.run(
        [ffmpeg.find("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=codec_name,width,height,nb_read_packets", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    s = json.loads(out.stdout)["streams"][0]
    return {"codec": s["codec_name"], "size": f"{s['width']}x{s['height']}", "packets": int(s["nb_read_packets"])}


# ------------------------------------------------------------------ A
def synthetic_tests(r: Report) -> None:
    r.info("")
    r.info("【A. 合成音測試】")
    quiet = lambda *_: None  # noqa: E731

    with tempfile.TemporaryDirectory(prefix="denki-selftest-") as tmp:
        tmp = Path(tmp)
        songs = tmp / "songs"
        src = tmp / f"{TEST_NAME}.mp4"

        try:
            make_test_video(src)
            r.ok("產生測試影片", src.name)
        except Exception as e:  # noqa: BLE001
            r.fail("產生測試影片", str(e))
            return

        # 1. 分析
        song = pipeline.analyze(src, backend="fake", songs_dir=songs, log=quiet)
        r.check("分析並保存中間檔", song.is_analyzed() and song.meta_path.exists(),
                f"{song.slug}（模式 {song.default_mode()}，{song.no_vocals.suffix}）")
        r.check("偵測到來源是影片", song.has_video, f"has_video={song.has_video}")

        # 2. 第二次不重跑
        m0 = song.default_mode()
        before = song.modes[m0]["analyzed_at"]
        t0 = time.perf_counter()
        again = pipeline.analyze(src, mode=m0, backend="fake", songs_dir=songs, log=quiet)
        r.check("第二次分析直接沿用", again.modes[m0]["analyzed_at"] == before,
                f"{time.perf_counter() - t0:.2f} 秒")

        # 3. 分離結果正確（伴奏≈220Hz、人聲≈440Hz）
        nv, v = peak_hz(middle(song.no_vocals)), peak_hz(middle(song.vocals))
        r.check("伴奏軌頻率", abs(nv - BASS_HZ) < 5, f"{nv:.0f} Hz（預期 {BASS_HZ:.0f}）")
        r.check("人聲軌頻率", abs(v - VOCAL_HZ) < 5, f"{v:.0f} Hz（預期 {VOCAL_HZ:.0f}）")

        # 4. 降 3 Key：220 → 185
        outs = pipeline.render(song, key=-3, guide=0, fmt="wav", log=quiet)
        got, want = peak_hz(middle(outs[0])), BASS_HZ * 2 ** (-3 / 12)
        r.check("降 3 Key", abs(got - want) < 4, f"{got:.0f} Hz（預期 {want:.0f}）")

        # 5. 升 5 Key：220 → 294
        outs = pipeline.render(song, key=5, guide=0, fmt="wav", log=quiet)
        got, want = peak_hz(middle(outs[0])), BASS_HZ * 2 ** (5 / 12)
        r.check("升 5 Key", abs(got - want) < 5, f"{got:.0f} Hz（預期 {want:.0f}）")

        # 6. 導唱 0% 沒有人聲、100% 有人聲
        o0 = pipeline.render(song, key=0, guide=0, fmt="wav", log=quiet)[0]
        o100 = pipeline.render(song, key=0, guide=100, fmt="wav", log=quiet)[0]
        base = band_energy(middle(song.no_vocals), VOCAL_HZ)
        e0, e100 = band_energy(middle(o0), VOCAL_HZ), band_energy(middle(o100), VOCAL_HZ)
        r.check("導唱 0% 沒有混入人聲", e0 <= base + 0.01, f"人聲比例 {e0:.1%}（伴奏中間檔本身 {base:.1%}）")
        r.check("導唱 100% 有人聲", e100 > 0.25, f"人聲比例 {e100:.1%}")

        # 7. 導唱 + 變調同時生效：人聲 440 → 370
        o = pipeline.render(song, key=-3, guide=100, fmt="wav", log=quiet)[0]
        e = band_energy(middle(o), VOCAL_HZ * 2 ** (-3 / 12))
        r.check("導唱也跟著變調", e > 0.2, f"370Hz 比例 {e:.1%}")

        # 8. 影片只換音軌，畫面原封不動
        outs = pipeline.render(song, key=-3, guide=20, fmt="video", also_mp3=True, log=quiet)
        vid = next(p for p in outs if p.suffix == ".mp4")
        a, b = video_stream(src), video_stream(vid)
        r.check("影片畫面未重新編碼", a == b, f"原 {a['codec']} {a['packets']} 格 → 新 {b['codec']} {b['packets']} 格")
        got = peak_hz(middle(vid))
        r.check("影片音軌已降 Key", abs(got - BASS_HZ * 2 ** (-3 / 12)) < 4, f"{got:.0f} Hz")
        mp3 = next((p for p in outs if p.suffix == ".mp3"), None)
        r.check("同時輸出 MP3", bool(mp3 and mp3.stat().st_size > 1000), mp3.name if mp3 else "沒有產生")
        tag0 = config.MODES[song.default_mode()]["tag"]
        r.check("檔名規則", vid.name == f"{TEST_NAME}{tag0}_伴奏_-3key_導唱20.mp4", vid.name)

        # 9. 變速（練習用）
        o = pipeline.render(song, key=0, guide=0, tempo=80, fmt="wav", log=quiet)[0]
        d_src, d_out = ffmpeg.probe(song.no_vocals)["duration"], ffmpeg.probe(o)["duration"]
        r.check("速度 80%", abs(d_out - d_src / 0.8) < 0.3, f"{d_src:.1f} 秒 → {d_out:.1f} 秒")

        # 10. 參數保護
        try:
            pipeline.render(song, key=20, log=quiet)
            r.fail("Key 超出範圍會擋下", "沒有擋")
        except Exception as e:  # noqa: BLE001
            r.ok("Key 超出範圍會擋下", str(e))

        # 11. 找歌、清單、紀錄
        r.check("用歌名找得到", find_song("天使", songs) is not None, "「天使」")
        n = len(list_songs(songs)[0].meta.get("renders", []))
        r.check("輸出紀錄寫進 project.json", n >= 7, f"{n} 筆")

        # 12. 第二種模式：並存、不影響第一種
        first = song.default_mode()
        other = "harmony" if first != "harmony" else "standard"
        song = pipeline.analyze(src, mode=other, backend="fake", songs_dir=songs, log=quiet)
        modes = song.analyzed_modes()
        r.check("多種分離模式並存", first in modes and other in modes and len(modes) == 2, "、".join(modes))
        r.check("中間檔存成 FLAC", song.stem(other, "no_vocals").suffix == ".flac", song.stem(other, "no_vocals").name)
        outs = pipeline.render(song, mode=other, key=0, fmt="mp3", log=quiet)
        tag = config.MODES[other]["tag"]
        r.check("輸出檔名帶模式標記", tag in outs[0].name if tag else "_伴奏_" in outs[0].name, outs[0].name)

        # 13. 音量補償：去掉人聲變小聲 → 補回原曲響度
        src_lufs = song.meta.get("loudness")
        comp = song.compensation_db(first)
        o_off = pipeline.render(song, mode=first, fmt="wav", compensate=False, log=quiet)[0]
        l_off = ffmpeg.loudness(o_off)
        o_on = pipeline.render(song, mode=first, fmt="wav", compensate=True, log=quiet)[0]
        l_on = ffmpeg.loudness(o_on)
        r.check("量到原曲響度與需補償量", src_lufs is not None and comp > 1,
                f"原曲 {src_lufs} LUFS，伴奏需補 {comp:+.1f} dB（上限 {config.MAX_COMPENSATION_DB:.0f}）")
        applied = (l_on - l_off) if (l_on is not None and l_off is not None) else 0
        r.check("音量補償生效", abs(applied - comp) < 0.7,
                f"不補 {l_off} → 補償後 {l_on} LUFS，實際補了 {applied:+.1f} dB")
        l_g1 = ffmpeg.loudness(pipeline.render(song, mode=first, fmt="wav", guide=100, compensate=True, log=quiet)[0])
        l_g0 = ffmpeg.loudness(pipeline.render(song, mode=first, fmt="wav", guide=100, compensate=False, log=quiet)[0])
        r.check("導唱越多補越少（100% 不補）", l_g1 is not None and l_g0 is not None and abs(l_g1 - l_g0) < 0.3
                and abs(pipeline.effective_compensation(song, first, 50) - comp / 2) < 0.01,
                f"導唱 50% 補 {pipeline.effective_compensation(song, first, 50):+.1f} dB、100% 補 {l_g1 - l_g0:+.1f} dB")

        # 14. 預備拍（2.3）
        countin_tests(r, song, first, tmp, quiet)

        # 14. 清除中間檔：成品與紀錄保留
        keep = [Path(x["file"]) for x in song.meta["renders"]]
        before = song.cache_bytes()
        freed = song.clear_cache()
        song = Song.load(song.dir)
        r.check("清除中間檔", freed == before > 0 and song.cache_bytes() == 0 and not song.is_analyzed(),
                f"釋放 {freed / 1e6:.1f} MB")
        r.check("清除後成品與紀錄都在", all(p.exists() for p in keep) and len(song.meta["renders"]) == len(keep),
                f"{len(keep)} 個成品")
        r.check("清除後預備拍設定還在", bool(song.meta.get("beats")), f"♩{song.meta.get('beats', {}).get('bpm')}")
        again = pipeline.analyze(src, mode=first, backend="fake", songs_dir=songs, log=quiet)
        r.check("清除後可以重新分析", again.is_analyzed(first), first)

        # 15. 第 1、2 批的舊資料自動視為「標準」模式
        import json as _json
        import shutil as _shutil
        legacy = songs / "舊版的歌"
        (legacy / "stems").mkdir(parents=True)
        _shutil.copy(again.stem(first, "no_vocals"), legacy / "stems" / "no_vocals.wav")
        _shutil.copy(again.stem(first, "vocals"), legacy / "stems" / "vocals.wav")
        (legacy / "project.json").write_text(_json.dumps({
            "source": str(src), "title": "舊版的歌", "analyzed_at": "2026-09-19T06:41:00",
            "separation": {"backend": "demucs", "model": "htdemucs_ft", "device": "cuda", "seconds": 27.1},
            "renders": []}, ensure_ascii=False), encoding="utf-8")
        old = Song.load(legacy)
        r.check("舊資料相容", old.analyzed_modes() == ["standard"], f"{old.analyzed_modes()}")
        # 2.3.4：rotary 版本修正前做的高品質結果（沒有 rev）要當作沒分析，才會自動重做
        s2 = Song.load(again.dir)
        hq_info = s2.modes.get(first) or {}
        if first in config.MIN_SEP_REV:
            hq_info.pop("rev", None)
            s2.save()
            r.check("舊版高品質結果會自動重做", not Song.load(again.dir).is_analyzed(first), f"{first} 視為未分析")
            s2.modes[first]["rev"] = config.SEP_REV
            s2.save()
        # 舊資料沒有響度 → 第一次用到時補量，音量補償才不會變成 0
        no_lufs_before = old.meta.get("loudness") is None and old.compensation_db("standard") == 0
        pipeline.ensure_loudness(old)
        again_old = Song.load(legacy)
        r.check("舊資料補量響度", no_lufs_before and again_old.meta.get("loudness") is not None
                and again_old.compensation_db("standard") > 0,
                f"原曲 {again_old.meta.get('loudness')} LUFS，補償 {again_old.compensation_db('standard'):+.1f} dB")


# ------------------------------------------------------------------ 預備拍
def drum_loop(path: Path, bpm: float, start: float, seconds: float = 14, sr: int = 44100) -> None:
    """合成一段鼓組：大鼓 1、3 拍，小鼓 2、4 拍，腳踏鈸每半拍，外加貝斯。"""
    rng = np.random.default_rng(1)
    n = int(sr * seconds)
    y = np.zeros(n, np.float32)
    p = 60 / bpm
    t = np.arange(int(sr * 0.25)) / sr
    kick = (np.sin(2 * np.pi * (60 + 80 * np.exp(-t * 30)) * t) * np.exp(-t * 12)).astype(np.float32)
    snare = (rng.standard_normal(len(t)) * np.exp(-t * 25) * 0.5).astype(np.float32)
    hat = (rng.standard_normal(int(sr * 0.03)) * np.exp(-np.arange(int(sr * 0.03)) / sr * 150) * 0.2).astype(np.float32)
    i = 0
    while start + i * p / 2 < seconds - 0.3:
        at = int((start + i * p / 2) * sr)
        y[at:at + len(hat)] += hat[: n - at]
        if i % 2 == 0:
            s = kick if (i // 2) % 4 in (0, 2) else snare
            y[at:at + len(s)] += s[: n - at]
        i += 1
    tt = np.arange(n) / sr
    y += (0.15 * np.sin(2 * np.pi * 55 * tt) * (tt > start)).astype(np.float32)
    beats.write_wav(path, y / np.max(np.abs(y)) * 0.8, sr)


def first_clicks(path: Path, before: float, sr: int = 22050) -> list[float]:
    """找出 before 秒以前每一下鼓棒的位置。"""
    x = ffmpeg.decode_pcm(path, sr)[: int(before * sr)]
    a = np.abs(x)
    if not len(a) or a.max() <= 0:
        return []
    idx = np.nonzero(a > a.max() * 0.3)[0]
    out: list[int] = []
    for i in idx:
        if not out or i - out[-1] > sr * 0.1:
            out.append(int(i))
    return [i / sr for i in out]


def countin_tests(r: Report, song: Song, mode: str, tmp: Path, quiet) -> None:
    if not beats.available():
        r.info("⏭️ 這台電腦沒有 librosa，跳過預備拍測試（點「更新並測試.bat」會補裝）。")
        return
    # 偵測：96 BPM、第一拍在 0.4 秒
    drum = tmp / "鼓組測試.wav"
    drum_loop(drum, 96, 0.4)
    d = beats.detect(drum)
    r.check("偵測速度與第一拍", abs(d["bpm"] - 96) < 1 and abs(d["first_beat"] - 0.4) < 0.02,
            f"♩{d['bpm']}（預期 96）、第一拍 {d['first_beat']} 秒（預期 0.4）")
    fast = tmp / "快歌測試.wav"
    drum_loop(fast, 150, 0.6)
    d2 = beats.detect(fast)
    r.check("偵測快歌不會抓成半速", abs(d2["bpm"] - 150) < 1.5 and abs(d2["first_beat"] - 0.6) < 0.02,
            f"♩{d2['bpm']}（預期 150）、第一拍 {d2['first_beat']} 秒")
    # 點拍：手有點抖也要算得準
    jitter = np.random.default_rng(3).normal(0, 0.012, 8)
    taps = [0.4 + (5 + k) * 0.625 + j for k, j in enumerate(jitter)]   # 從第 6 拍開始跟著點
    tp = beats.from_taps(taps, near=0.4)
    r.check("點拍抓速度", abs(tp["bpm"] - 96) < 1.5 and abs(tp["first_beat"] - 0.4) < 0.03,
            f"♩{tp['bpm']}、第一拍 {tp['first_beat']} 秒")

    # 輸出：測試歌是 0 秒就開始的合成音，設成 120 BPM → 1 小節 = 2.0 秒
    pipeline.get_beats(song)
    pipeline.set_beats(song, bpm=120, first_beat=0.0)
    base = pipeline.render(song, mode=mode, fmt="wav", log=quiet)[0]
    d0 = ffmpeg.probe(base)["duration"]
    o = pipeline.render(song, mode=mode, fmt="mp3", countin=1, log=quiet)[0]
    d1 = ffmpeg.probe(o)["duration"]
    clicks = first_clicks(o, 1.9)
    grid_ok = len(clicks) == 4 and all(abs(c - k * 0.5) < 0.03 for k, c in enumerate(clicks))
    r.check("預備拍輸出（1 小節）", abs((d1 - d0) - 2.0) < 0.08 and grid_ok,
            f"長度 {d0:.2f} → {d1:.2f} 秒，鼓棒在 {', '.join(f'{c:.2f}' for c in clicks)} 秒")
    r.check("預備拍檔名", o.name.endswith("_預備拍.mp3"), o.name)
    o2 = pipeline.render(song, mode=mode, fmt="wav", countin=2, log=quiet)[0]
    r.check("預備拍 2 小節", abs((ffmpeg.probe(o2)["duration"] - d0) - 4.0) < 0.08, f"多 {ffmpeg.probe(o2)['duration'] - d0:.2f} 秒（預期 4.0）")
    o3 = pipeline.render(song, mode=mode, fmt="wav", countin=1, tempo=80, log=quiet)[0]
    base80 = pipeline.render(song, mode=mode, fmt="wav", tempo=80, log=quiet)[0]
    extra = ffmpeg.probe(o3)["duration"] - ffmpeg.probe(base80)["duration"]
    r.check("預備拍跟著速度變慢", abs(extra - 2.5) < 0.1, f"速度 80% 時多 {extra:.2f} 秒（預期 2.5）")
    outs = pipeline.render(song, mode=mode, fmt="video", also_mp3=True, countin=1, log=quiet)
    vid = next(p for p in outs if p.suffix == ".mp4")
    mp3 = next(p for p in outs if p.suffix == ".mp3")
    dv = ffmpeg.probe(vid)["duration"]
    dm = ffmpeg.probe(mp3)["duration"]
    r.check("影片維持原長度、同時輸出的 MP3 有預備拍",
            abs(dv - d0) < 0.15 and abs((dm - d0) - 2.0) < 0.1 and "_預備拍" not in vid.name and mp3.name.endswith("_預備拍.mp3"),
            f"影片 {dv:.2f} 秒、MP3 {dm:.2f} 秒")
    pipeline.reset_beats(song)

# ------------------------------------------------------------------ B
def real_test(r: Report) -> None:
    r.info("")
    r.info("【B. 真實歌曲測試（AI 分離）】")
    if not separate.demucs_available():
        r.info("⏭️ 這台電腦沒有 Demucs，跳過。")
        return

    candidates = [
        p for p in sorted(config.ROOT.iterdir())
        if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXTS and "_伴奏_" not in p.stem
    ] if config.ROOT.exists() else []
    if not candidates:
        r.info(f"⏭️ {config.ROOT} 裡沒有歌，跳過。放一首 mp3 或 mp4 進去再跑一次。")
        return

    src = candidates[0]
    r.info(f"使用：{src.name}")
    if shutil.which("nvidia-smi"):
        import torch
        r.check("顯卡版 PyTorch", torch.cuda.is_available(),
                f"{torch.__version__}，{torch.cuda.get_device_name(0)}" if torch.cuda.is_available()
                else f"{torch.__version__}：這台電腦有 NVIDIA 顯卡，但 PyTorch 是 CPU 版（重新點一次 更新並測試.bat 會自動修復）")
    if separate.roformer_available():
        from importlib import metadata as _md
        try:
            rot = _md.version("rotary-embedding-torch")
        except Exception:  # noqa: BLE001
            rot = "沒有安裝"
        r.check("高品質元件版本正確", rot == "0.6.5",
                f"rotary-embedding-torch {rot}" + ("" if rot == "0.6.5" else "（要 0.6.5，請重新點 更新並測試.bat）"))
    status = pipeline.mode_status()
    for m in config.MODE_ORDER:
        st = status[m]
        r.info(f"  {config.MODES[m]['label']}：{'可用' if st['available'] else '不可用（' + st['reason'] + '）'}")
    mode = pipeline.default_mode()
    try:
        t0 = time.perf_counter()
        r.info(f"用「{config.MODES[mode]['label']}」模式分析（第一次使用會先下載模型，請稍候）…")
        song = pipeline.analyze(src, mode=mode)
        info = song.modes.get(mode, {})
        r.ok(f"AI 分離（{config.MODES[mode]['label']}）",
             f"{info.get('seconds', '?')} 秒，{info.get('device', '?')}，模型 {info.get('model', '?')}"
             + (f"（另外第一次下載模型 {info['download_seconds']} 秒）" if info.get("download_seconds") else "")
             if time.perf_counter() - t0 > 3 else "沿用之前的分析結果")
        v_lufs = song.modes[mode].get("v_lufs")
        r.check("人聲軌真的有聲音", v_lufs is not None and v_lufs > -45,
                f"{v_lufs} LUFS" if v_lufs is not None else "量不到（人聲軌是靜音 → 分離失敗）")
        r.check("音量補償量", song.meta.get("loudness") is not None and song.modes[mode].get("nv_lufs") is not None,
                f"原曲 {song.meta.get('loudness')} LUFS，伴奏需補 {song.compensation_db(mode):+.1f} dB")
        t0 = time.perf_counter()
        outs = pipeline.render(song, mode=mode, key=-3, guide=0, also_mp3=False)
        r.ok("降 3 Key 輸出", f"{time.perf_counter() - t0:.1f} 秒")
        for o in outs:
            r.info(f"    {o}")
    except Exception as e:  # noqa: BLE001
        r.fail("真實歌曲測試", f"{e}")
        r.info(traceback.format_exc()[-1500:])


# ------------------------------------------------------------------ C
def ui_tests(r: Report) -> None:
    r.info("")
    r.info("【C. 桌面介面】")
    dist = ROOT_DIR / "ui" / "dist"
    r.check("介面檔案", (dist / "index.html").exists() and (dist / "vendor" / "SignalsmithStretch.mjs").exists(),
            "ui/dist 與即時變調元件都在" if (dist / "index.html").exists() else "找不到 ui/dist")
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        r.ok("後端套件", f"fastapi {fastapi.__version__}")
    except ImportError as e:
        r.fail("後端套件", f"{e}（請重新執行「更新並測試.bat」補裝）")
        return
    try:
        import webview
        r.ok("視窗套件", f"pywebview {getattr(webview, '__version__', '')}".strip())
    except Exception as e:  # noqa: BLE001
        r.fail("視窗套件", f"{e}")

    import json as _json
    import urllib.request

    from karaoke.server import serve
    try:
        url, server = serve()
        with urllib.request.urlopen(url + "/api/status", timeout=10) as res:
            st = _json.loads(res.read())
        with urllib.request.urlopen(url + "/", timeout=10) as res:
            html = res.read().decode("utf-8", "replace")
        r.check("後端啟動", st.get("version") == __version__, f"{url}，{st.get('deviceName')}")
        r.check("介面可以載入", "<div id=\"root\">" in html, "index.html")
        server.should_exit = True
    except Exception as e:  # noqa: BLE001
        r.fail("後端啟動", str(e))

    # 2.3.3：視窗「沒有回應」的真兇與幫兇，不能再回來
    from karaoke.app import Api
    public = [n for n in vars(Api()) if not n.startswith("_")]
    r.check("介面橋接物件沒有公開欄位（避免視窗卡死）", not public, "只有方法" if not public else f"多了：{public}")
    import subprocess as _sp
    probe = ("import sys; from karaoke import server, beats; from karaoke.hardware import detect_device; "
             "server.create_app(); detect_device(); beats.available(); "
             "print('torch' in sys.modules, 'librosa' in sys.modules)")
    out = _sp.run([sys.executable, "-c", probe], cwd=ROOT_DIR, capture_output=True, text=True).stdout.strip()
    r.check("啟動時不在程式內載入 PyTorch／librosa", out.endswith("False False"),
            "都放到子程序" if out.endswith("False False") else f"torch、librosa 載入狀態：{out}")


# ------------------------------------------------------------------ D
def install_tests(r: Report) -> None:
    """第 3 批：安裝程式、可攜路徑、程式內更新。"""
    import zipfile

    from karaoke import deps, updater

    r.info("")
    r.info("【D. 安裝與更新】")
    r.info(f"安裝資料夾：{config.ROOT}（Python：{sys.executable}）")
    if not os.environ.get("DENKI_KARAOKE_HOME"):
        r.check("路徑跟著程式走（可攜）", config.ROOT == config.APP_DIR.parent, f"{config.APP_DIR} → {config.ROOT}")
    if sys.platform == "win32":
        bad = deps.check()
        r.check("套件版本和鎖定清單一致", not bad,
                f"{len(deps.read_lock()) + 2} 個都正確" if not bad else "；".join(bad[:5]) + "（請點 更新並測試.bat 修正）")
        import subprocess as _sp
        from karaoke.proc import NO_WINDOW
        out = _sp.run([sys.executable, "-c", "import pythonnet; pythonnet.load(); import clr; print('ok')"],
                      capture_output=True, text=True, **NO_WINDOW)
        r.check("視窗元件（.NET）可以載入", out.stdout.strip().endswith("ok"), (out.stderr or out.stdout).strip()[-200:] or "OK")
        r.check("WebView2 執行階段", _webview2_version() is not None, _webview2_version() or "沒有安裝（請重新執行 安裝.bat）")
    # 版本比較
    cases = [("v0.3.1", "0.3.0", True), ("v0.3.0", "0.3.0", False), ("v0.10.0", "0.9.9", True),
             ("v0.3.0", "0.2.3.4", True), ("v0.2.9", "0.3.0", False)]
    wrong = [c for c in cases if updater.is_newer(c[0], c[1]) != c[2]]
    r.check("更新：版本號比較", not wrong, "v0.10.0 比 0.9.9 新、v0.3.0 比 0.2.3.4 新" if not wrong else f"算錯：{wrong}")
    # 下載的壓縮檔 → 找到程式、核對版本
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "x.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for rel in ("karaoke/__init__.py", "karaoke/app.py", "karaoke/deps.py", "requirements-lock.txt", "ui/dist/index.html"):
                src = ROOT_DIR / rel
                zf.write(src, f"hansonfan77-oss-denki-karaoke-abc/{rel}")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(Path(tmp) / "new")
        try:
            app_new = updater.find_app_root(Path(tmp) / "new")
            updater.verify_new_app(app_new, "v" + __version__)
            r.ok("更新：新版壓縮檔核對", "找得到程式、版本號一致")
        except Exception as e:  # noqa: BLE001
            r.fail("更新：新版壓縮檔核對", str(e))
    ok, why = updater.enabled()
    r.info(f"  程式內更新：{'開啟' if ok else '關閉（' + why + '）'}")


def _webview2_version():
    import winreg
    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
                      (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
                      (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}")):
        try:
            with winreg.OpenKey(hive, key) as k:
                v = winreg.QueryValueEx(k, "pv")[0]
                if v and v != "0.0.0.0":
                    return v
        except OSError:
            continue
    return None


# ------------------------------------------------------------------ main
def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="也跑真實歌曲 AI 分離")
    ap.add_argument("--report", type=Path, default=None, help="結果檔位置")
    args = ap.parse_args()

    r = Report()
    dev = detect_device()
    r.info(f"DENKI 伴奏工具 v{__version__} 自動測試　{datetime.now():%Y-%m-%d %H:%M}")
    r.info(f"運算裝置：{dev['name']}")
    try:
        r.info(f"FFmpeg：{ffmpeg.find('ffmpeg')}（rubberband：{'有' if ffmpeg.has_filter('rubberband') else '沒有'}）")
    except ffmpeg.FFmpegError as e:
        r.fail("FFmpeg", str(e))

    try:
        synthetic_tests(r)
    except Exception as e:  # noqa: BLE001
        r.fail("合成音測試中斷", str(e))
        r.info(traceback.format_exc()[-1500:])

    try:
        from tests.lyrics_selftest import karaoke_video_tests, lyrics_tests
        lyrics_tests(r, make_test_video)
        karaoke_video_tests(r, make_test_video)
    except Exception as e:  # noqa: BLE001
        r.fail("歌詞與對時間測試中斷", str(e))
        r.info(traceback.format_exc()[-1500:])

    if args.real:
        real_test(r)

    try:
        ui_tests(r)
    except Exception as e:  # noqa: BLE001
        r.fail("介面檢查中斷", str(e))

    try:
        install_tests(r)
    except Exception as e:  # noqa: BLE001
        r.fail("安裝與更新檢查中斷", str(e))
        r.info(traceback.format_exc()[-1500:])

    r.info("")
    r.info(f"總結：{r.passed} 項通過，{r.failed} 項失敗" + ("　🎉 全部通過" if r.failed == 0 else ""))

    report = args.report or (config.ROOT / "結果.txt")
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(r.lines) + "\n", encoding="utf-8-sig")
        print(f"\n結果已寫入：{report}")
    except OSError as e:
        print(f"\n（結果檔寫入失敗：{e}）")
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
