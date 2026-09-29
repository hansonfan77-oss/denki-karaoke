"""本機後端（只聽 127.0.0.1）。介面（React）透過這些 API 呼叫 pipeline。

GET    /api/status                            版本、顯卡、各分離模式能不能用
GET    /api/songs                             最近處理的歌
GET    /api/songs/{slug}                      一首歌的資料
GET    /api/songs/{slug}/stems/{mode}/{name}  中間檔（no_vocals / vocals），預覽播放用
POST   /api/analyze            {path, mode?}  開始分析新檔案 → {job}
POST   /api/songs/{slug}/analyze {mode}       已有的歌再分析另一種模式（預覽畫面切換用）
PUT    /api/upload?name=…&mode=…              瀏覽器模式拿不到檔案路徑時，先上傳再分析
GET    /api/jobs/{id}                         分析進度
POST   /api/jobs/{id}/cancel                  取消
POST   /api/songs/{slug}/render  {...}        輸出一個版本
GET    /api/render-name                       輸出檔名預覽
GET    /api/songs/{slug}/beats                速度與第一拍（第一次會偵測，約幾秒）
PUT    /api/songs/{slug}/beats  {bpm?, firstBeat?, taps?}  手動修正
DELETE /api/songs/{slug}/beats                回到 AI 偵測的結果
GET    /api/sticks.wav                        鼓棒聲（預覽用）
GET    /api/cache                             中間檔總佔用
DELETE /api/songs/{slug}/cache                清除一首歌的中間檔
DELETE /api/cache                             清除全部中間檔
GET/POST /api/settings                        輸出資料夾等設定
POST   /api/open               {path}         用系統開啟檔案或資料夾
GET    /api/update?refresh=1                  更新狀態（refresh=1 立刻問 GitHub）
POST   /api/update/start                      下載新版並交給更新小幫手（伴奏工具會自動關掉再開）
POST   /api/update/ack                        上次更新的結果已經顯示過了

第 4 批（卡拉影片：歌詞與對時間）
GET    /api/lyrics/search?title=&artist=&duration=   搜尋 LRCLIB
POST   /api/lyrics/parse-lrc   {text}         LRC 文字 → 句子與時間
GET    /api/songs/{slug}/lyrics               這首歌存好的歌詞（沒有回傳 null）＋猜的歌名
PUT    /api/songs/{slug}/lyrics {lines, offset, source, lang?, lrclib?}  存歌詞（對時間畫面自動存）
DELETE /api/songs/{slug}/lyrics               刪掉歌詞，重新選來源
GET    /api/align/status                      AI 對時間元件與模型是否就緒
POST   /api/songs/{slug}/align {text, lang?}  開始 AI 對時間 → {job}
GET    /api/align-jobs/{id}                   對時間進度
POST   /api/align-jobs/{id}/cancel            取消

第 5 批（卡拉影片：字幕樣式與輸出）
GET    /api/songs/{slug}/karaoke              樣式、背景、伴奏設定＋可用的字型、背景選項
PUT    /api/songs/{slug}/karaoke {style?, background?, audio?, alsoLrc?}  存設定（樣式同時記成下一首的預設）
PUT    /api/songs/{slug}/bg-image?name=…      上傳背景圖片
GET    /api/songs/{slug}/bg-image | cover | video   預覽用：背景圖、專輯封面、原影片
POST   /api/songs/{slug}/karaoke-render {outDir?}   輸出卡拉影片 → {job}
GET    /api/karaoke-jobs/{id}                 輸出進度
POST   /api/karaoke-jobs/{id}/cancel          取消
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, align, beats, config, export, ffmpeg, kvideo, lyrics, pipeline, separate, updater
from .hardware import detect_device
from .mix import MixError
from .project import STEM_NAMES, Song, list_songs, slugify

UI_DIST = Path(__file__).resolve().parents[1] / "ui" / "dist"
SETTINGS_PATH = config.ROOT / "settings.json"
BACKEND = os.environ.get("DENKI_BACKEND") or None  # 測試時設成 fake；正式使用依模式決定


# ------------------------------------------------------------------ 共用
def song_summary(s: Song) -> dict:
    modes = {}
    for m in config.MODE_ORDER:
        info = s.modes.get(m, {})
        modes[m] = {
            "analyzed": s.is_analyzed(m),
            "seconds": info.get("seconds"),
            "device": info.get("device"),
            "compensationDb": s.compensation_db(m),
        }
    return {
        "slug": s.slug,
        "title": s.title,
        "analyzed": s.is_analyzed(),
        "analyzedModes": s.analyzed_modes(),
        "lastMode": s.default_mode(),
        "modes": modes,
        "hasVideo": s.has_video,
        "duration": s.meta.get("duration"),
        "source": s.meta.get("source"),
        "sourceExists": Path(s.meta.get("source", "")).exists(),
        "renders": s.meta.get("renders", [])[-10:],
        "outDir": str(s.out_dir),
        "cacheBytes": s.cache_bytes(),
        "lyricsLines": _lyrics_count(s),
    }


def _lyrics_count(s: Song) -> int:
    p = lyrics.lyrics_path(s)
    if not p.exists():
        return 0
    data = lyrics.load(s)
    return len(data["lines"]) if data else 0


def get_song(slug: str) -> Song:
    d = config.SONGS_DIR / slug
    if "/" in slug or "\\" in slug or ".." in slug or not (d / "project.json").exists():
        raise HTTPException(404, f"找不到歌曲：{slug}")
    song = Song.load(d)
    pipeline.ensure_loudness(song)  # 舊版分析的歌第一次打開時補量響度（之後直接讀紀錄）
    return song


def check_mode(mode: Optional[str]) -> str:
    mode = mode or pipeline.default_mode(BACKEND)
    if mode not in config.MODES:
        raise HTTPException(400, f"不認得的分離模式：{mode}")
    st = pipeline.mode_status(BACKEND)[mode]
    if not st["available"]:
        raise HTTPException(400, f"「{config.MODES[mode]['label']}」模式在這台電腦不能用：{st['reason']}")
    return mode


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ 分析工作
@dataclass
class Job:
    id: str
    source: str
    mode: str
    state: str = "running"           # running / done / error / cancelled
    stage: str = "read"              # read / separate / save
    progress: float = 0.0
    message: str = "讀取檔案…"
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    slug: Optional[str] = None
    error: Optional[str] = None
    device: str = ""
    _cancel: separate.CancelToken = field(default_factory=separate.CancelToken, repr=False)

    def public(self) -> dict:
        d = {f: getattr(self, f) for f in self.__dataclass_fields__ if not f.startswith("_")}
        now = self.finished or time.time()
        d["elapsed"] = round(now - self.started, 1)
        d["modeLabel"] = config.MODES[self.mode]["label"]
        # 預估剩餘：分離階段有了進度之後用線性外插
        if self.state == "running" and self.stage == "separate" and self.progress > 3:
            spent = now - getattr(self, "_sep_started", self.started)
            d["eta"] = round(spent * (100 - self.progress) / self.progress, 0)
        else:
            d["eta"] = None
        return d


JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _run_job(job: Job) -> None:
    def on_progress(pct: float, msg: str) -> None:
        if job.stage != "separate":
            job.stage = "separate"
            job._sep_started = time.time()  # type: ignore[attr-defined]
        if pct > 0 or job.progress == 0:
            job.progress = round(pct, 1)
        job.message = msg

    def on_log(msg: str) -> None:
        if "保存中間檔" in msg:
            job.stage, job.progress, job.message = "save", 99.0, "保存中間檔…"

    try:
        song = pipeline.analyze(job.source, mode=job.mode, backend=BACKEND, log=on_log,
                                progress=on_progress, cancel=job._cancel)
        job.slug = song.slug
        job.progress, job.state, job.message = 100.0, "done", "分析完成"
    except separate.Cancelled:
        job.state, job.message = "cancelled", "已取消"
    except Exception as e:  # noqa: BLE001 — 任何錯誤都要回報到畫面上
        job.state, job.error = "error", str(e)
    finally:
        job.finished = time.time()


def start_job(source: Path, mode: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:10], source=str(source), mode=mode, device=detect_device()["name"])
    with _JOBS_LOCK:
        # 同一個檔案、同一個模式已經在跑就不要重複開
        for j in JOBS.values():
            if j.state == "running" and j.source == job.source and j.mode == mode:
                return j
        JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


# ------------------------------------------------------------------ AI 對時間工作（第 4 批）
@dataclass
class AlignJob:
    id: str
    slug: str
    state: str = "running"           # running / done / error / cancelled
    stage: str = "prepare"           # prepare / download / load / align / save
    progress: float = 0.0
    message: str = "準備中…"
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    error: Optional[str] = None
    lyrics: Optional[dict] = None
    _cancel: align.CancelToken = field(default_factory=align.CancelToken, repr=False)

    def public(self) -> dict:
        d = {f: getattr(self, f) for f in self.__dataclass_fields__ if not f.startswith("_")}
        d["elapsed"] = round((self.finished or time.time()) - self.started, 1)
        return d


ALIGN_JOBS: dict[str, AlignJob] = {}


def _run_align(job: AlignJob, song: Song, text: str, lang: Optional[str]) -> None:
    def on_progress(stage: str, pct: float, msg: str) -> None:
        job.stage, job.progress, job.message = stage, round(pct, 1), msg

    try:
        job.lyrics = align.align_song(song, text, lang=lang, progress=on_progress, cancel=job._cancel)
        job.state, job.progress, job.message = "done", 100.0, "完成"
    except align.Cancelled:
        job.state, job.message = "cancelled", "已取消"
    except Exception as e:  # noqa: BLE001 — 任何錯誤都要回報到畫面上
        logging.exception("AI 對時間失敗")
        job.state, job.error = "error", str(e)
    finally:
        job.finished = time.time()


# ------------------------------------------------------------------ 卡拉影片輸出工作（第 5 批）
@dataclass
class KaraokeJob:
    id: str
    slug: str
    state: str = "running"
    progress: float = 0.0
    message: str = "準備中…"
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    error: Optional[str] = None
    result: Optional[dict] = None
    _cancel: kvideo.CancelToken = field(default_factory=kvideo.CancelToken, repr=False)

    def public(self) -> dict:
        d = {f: getattr(self, f) for f in self.__dataclass_fields__ if not f.startswith("_")}
        now = self.finished or time.time()
        d["elapsed"] = round(now - self.started, 1)
        d["eta"] = (round((now - self.started) * (100 - self.progress) / self.progress)
                    if self.state == "running" and self.progress > 8 else None)
        return d


KARAOKE_JOBS: dict[str, KaraokeJob] = {}


def _run_karaoke(job: KaraokeJob, song: Song, out_dir: Optional[str]) -> None:
    def on_progress(pct: float, msg: str) -> None:
        job.progress, job.message = round(pct, 1), msg

    try:
        job.result = kvideo.render(song, out_dir=Path(out_dir) if out_dir else None,
                                   progress=on_progress, cancel=job._cancel)
        job.state, job.progress, job.message = "done", 100.0, "完成"
    except kvideo.Cancelled:
        job.state, job.message = "cancelled", "已取消"
    except Exception as e:  # noqa: BLE001
        logging.exception("卡拉影片輸出失敗")
        job.state, job.error = "error", str(e)
    finally:
        job.finished = time.time()


def karaoke_public(s: Song) -> dict:
    st = kvideo.load_settings(s)
    out_dir = load_settings().get("outDir") or str(s.out_dir)
    return {
        **st,
        "fonts": [{"id": k, "label": v["label"], "family": v["family"]} for k, v in kvideo.FONTS.items()],
        "resolvedBg": {k: v for k, v in kvideo.resolve_bg(s, st["background"]).items() if k != "path"},
        "hasVideo": s.has_video and s.source_path.exists(),
        "videoPreview": s.has_video and s.source_path.suffix.lower() in {".mp4", ".m4v", ".mov", ".webm"},
        "hasCover": kvideo.cover_path(s) is not None,
        "hasImage": kvideo.bg_image_path(s) is not None,
        "outName": kvideo.output_name(s, st["audio"]["key"], st["audio"]["guide"]),
        "outDir": out_dir,
        "analyzedModes": s.analyzed_modes(),
        "compensationDb": {m: s.compensation_db(m) for m in s.analyzed_modes()},
    }


# ------------------------------------------------------------------ API
class AnalyzeBody(BaseModel):
    path: str
    mode: Optional[str] = None


class ModeBody(BaseModel):
    mode: str


class RenderBody(BaseModel):
    mode: Optional[str] = None
    key: int = 0
    guide: int = 0
    format: str = "auto"
    alsoMp3: bool = False
    tempo: int = 100
    compensate: bool = True
    countin: int = 0
    outDir: Optional[str] = None


class BeatsBody(BaseModel):
    bpm: Optional[float] = None
    firstBeat: Optional[float] = None
    taps: Optional[list[float]] = None


def beats_public(b: dict) -> dict:
    return {"bpm": b["bpm"], "firstBeat": b["first_beat"], "musicStart": b.get("music_start", 0.0),
            "manual": bool(b.get("manual")),
            "auto": {"bpm": b["auto"]["bpm"], "firstBeat": b["auto"]["first_beat"]}}


class OpenBody(BaseModel):
    path: str


class SettingsBody(BaseModel):
    outDir: Optional[str] = None


class LrcBody(BaseModel):
    text: str


class LyricLine(BaseModel):
    t: float
    end: Optional[float] = None
    text: str


class LyricsBody(BaseModel):
    lines: list[LyricLine]
    offset: float = 0.0
    source: str = "manual"
    lang: Optional[str] = None
    lrclib: Optional[dict] = None


class KaraokeRenderBody(BaseModel):
    outDir: Optional[str] = None


class AlignBody(BaseModel):
    text: str
    lang: Optional[str] = None


def create_app() -> FastAPI:
    from .hardware import warm_up
    warm_up()   # 背景先查顯卡（子程序，幾秒），第一次開畫面時多半已經查好
    app = FastAPI(title="DENKI 伴奏工具", version=__version__)
    updater.busy_check = lambda: any(j.state == "running" for j in [*JOBS.values(), *ALIGN_JOBS.values(),
                                                                     *KARAOKE_JOBS.values()])
    if os.environ.get("DENKI_UPDATE_AUTO", "1") != "0" and updater.enabled()[0]:
        updater.check_in_background()   # 開啟時自動檢查（背景，連不上網也不影響）

    @app.get("/api/status")
    def status():
        dev = detect_device()
        try:
            rb = ffmpeg.has_filter("rubberband")
            ff = True
        except ffmpeg.FFmpegError:
            rb, ff = False, False
        st = pipeline.mode_status(BACKEND)
        return {
            "version": __version__,
            "device": dev["device"],
            "deviceName": dev["name"],
            "ffmpeg": ff,
            "rubberband": rb,
            "modes": [{
                "id": m, "label": config.MODES[m]["label"], "desc": config.MODES[m]["desc"],
                "available": st[m]["available"], "reason": st[m]["reason"],
                "sizeMb": config.MODES[m].get("size_mb"),
            } for m in config.MODE_ORDER],
            "defaultMode": pipeline.default_mode(BACKEND),
            "beatsAvailable": beats.available(),
            "root": str(config.ROOT),
        }

    @app.get("/api/songs")
    def songs():
        return [song_summary(s) for s in list_songs()[:30]]

    @app.get("/api/songs/{slug}")
    def song(slug: str):
        return song_summary(get_song(slug))

    @app.get("/api/songs/{slug}/stems/{mode}/{name}")
    def stem(slug: str, mode: str, name: str):
        s = get_song(slug)
        if mode not in config.MODES or name not in STEM_NAMES:
            raise HTTPException(404, "中間檔不存在")
        path = s.stem(mode, name)
        if not path.exists():
            raise HTTPException(404, "中間檔不存在")
        media = "audio/flac" if path.suffix.lower() == ".flac" else "audio/wav"
        return FileResponse(path, media_type=media)

    @app.post("/api/analyze")
    def analyze(body: AnalyzeBody):
        src = Path(body.path.strip().strip('"'))
        if not src.exists():
            raise HTTPException(400, f"找不到檔案：{src}")
        if src.suffix.lower() not in config.SUPPORTED_EXTS:
            raise HTTPException(400, f"不支援的格式：{src.suffix}")
        return start_job(src, check_mode(body.mode)).public()

    @app.post("/api/songs/{slug}/analyze")
    def analyze_song(slug: str, body: ModeBody):
        s = get_song(slug)
        mode = check_mode(body.mode)
        if not s.source_path.exists():
            raise HTTPException(400, f"找不到原始檔案：{s.source_path}，請重新拖入這首歌。")
        return start_job(s.source_path, mode).public()

    @app.put("/api/upload")
    async def upload(request: Request, name: str, mode: Optional[str] = None):
        safe = slugify(Path(name).stem) + Path(name).suffix.lower()
        if Path(safe).suffix not in config.SUPPORTED_EXTS:
            raise HTTPException(400, f"不支援的格式：{Path(name).suffix}")
        mode = check_mode(mode)
        inbox = config.ROOT / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / safe
        t0, size = time.perf_counter(), 0
        with dest.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                size += len(chunk)
        secs = time.perf_counter() - t0
        logging.info("上傳完成：%s，%.1f MB，%.1f 秒", safe, size / 1e6, secs)
        return start_job(dest, mode).public()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "找不到這個工作")
        return j.public()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        j = JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "找不到這個工作")
        j._cancel.cancel()
        return j.public()

    @app.post("/api/songs/{slug}/render")
    def render(slug: str, body: RenderBody):
        s = get_song(slug)
        out_dir = body.outDir or load_settings().get("outDir") or None
        try:
            outs = pipeline.render(s, mode=body.mode, key=body.key, guide=body.guide, fmt=body.format,
                                   also_mp3=body.alsoMp3, tempo=body.tempo, compensate=body.compensate,
                                   countin=body.countin,
                                   out_dir=Path(out_dir) if out_dir else None, log=lambda *_: None)
        except (pipeline.PipelineError, MixError, ffmpeg.FFmpegError) as e:
            raise HTTPException(400, str(e)) from e
        return {
            "files": [{"path": str(o), "name": o.name, "size": o.stat().st_size,
                       "kind": "video" if o.suffix.lower() in config.VIDEO_EXTS else "audio"} for o in outs],
            "outDir": str(outs[0].parent) if outs else None,
        }

    @app.get("/api/render-name")
    def render_name(slug: str, key: int = 0, guide: int = 0, tempo: int = 100,
                    format: str = "auto", mode: Optional[str] = None, countin: int = 0):
        s = get_song(slug)
        fmt = ("video" if s.has_video else "mp3") if format == "auto" else format
        ext = export.video_container_ext(s.source_path) if fmt == "video" else f".{fmt}"
        tag = config.MODES.get(mode or s.default_mode() or "standard", config.MODES["standard"])["tag"]
        out_dir = load_settings().get("outDir") or str(s.out_dir)
        return {"name": export.output_name(s.title, key, guide, tempo if fmt != "video" else 100, ext, tag,
                                           bool(countin) and fmt != "video"),
                "outDir": out_dir}

    @app.get("/api/songs/{slug}/beats")
    def get_beats(slug: str, redetect: bool = False):
        s = get_song(slug)
        try:
            return beats_public(pipeline.get_beats(s, redetect=redetect))
        except pipeline.PipelineError as e:
            raise HTTPException(400, str(e)) from e

    @app.put("/api/songs/{slug}/beats")
    def put_beats(slug: str, body: BeatsBody):
        s = get_song(slug)
        try:
            return beats_public(pipeline.set_beats(s, bpm=body.bpm, first_beat=body.firstBeat, taps=body.taps))
        except pipeline.PipelineError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/songs/{slug}/beats")
    def del_beats(slug: str):
        s = get_song(slug)
        try:
            return beats_public(pipeline.reset_beats(s))
        except pipeline.PipelineError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/sticks.wav")
    def sticks():
        path = config.ROOT / "cache" / "sticks.wav"
        if not path.exists():
            beats.write_wav(path, beats.stick_sample(), channels=1)
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/cache")
    def cache():
        songs = list_songs()
        return {"bytes": sum(s.cache_bytes() for s in songs),
                "songs": sum(1 for s in songs if s.cache_bytes() > 0)}

    @app.delete("/api/songs/{slug}/cache")
    def clear_song(slug: str):
        s = get_song(slug)
        return {"freed": s.clear_cache(), "song": song_summary(s)}

    @app.delete("/api/cache")
    def clear_all():
        running = {j.source for j in JOBS.values() if j.state == "running"}
        freed = 0
        for s in list_songs():
            if s.meta.get("source") not in running:
                freed += s.clear_cache()
        return {"freed": freed}

    @app.get("/api/settings")
    def get_settings():
        return load_settings()

    @app.post("/api/settings")
    def set_settings(body: SettingsBody):
        data = load_settings()
        data["outDir"] = body.outDir or None
        save_settings(data)
        return data

    @app.post("/api/open")
    def open_path(body: OpenBody):
        p = Path(body.path)
        if not p.exists():
            raise HTTPException(404, f"不存在：{p}")
        if os.name == "nt":
            os.startfile(str(p))  # noqa: S606 — 本機單人工具，只開啟已存在的路徑
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}

    @app.get("/api/update")
    def update_status(refresh: bool = False):
        if refresh and updater.enabled()[0]:
            return updater.check()
        return updater.public()

    @app.post("/api/update/start")
    def update_start():
        try:
            return updater.start()
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/update/ack")
    def update_ack():
        updater.ack_result()
        return {"ok": True}

    # ---------------- 第 4 批：歌詞與對時間
    @app.get("/api/lyrics/search")
    def lyrics_search(title: str, artist: str = "", duration: Optional[float] = None):
        try:
            return {"results": lyrics.search_lrclib(title, artist, duration)}
        except lyrics.LyricsError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/lyrics/parse-lrc")
    def parse_lrc(body: LrcBody):
        lines = lyrics.parse_lrc(body.text)
        if not lines:
            raise HTTPException(400, "這個檔案裡找不到附時間的歌詞（LRC 格式像 [00:12.34]歌詞）。")
        return {"lines": lines, "lang": lyrics.detect_language(" ".join(ln["text"] for ln in lines))}

    @app.get("/api/songs/{slug}/lyrics")
    def get_lyrics(slug: str):
        s = get_song(slug)
        return {"lyrics": lyrics.load(s), "titleGuess": lyrics.guess_title(s.title)}

    @app.put("/api/songs/{slug}/lyrics")
    def put_lyrics(slug: str, body: LyricsBody):
        s = get_song(slug)
        try:
            return lyrics.save(s, [ln.model_dump() for ln in body.lines], source=body.source,
                               offset=body.offset, lang=body.lang, lrclib=body.lrclib)
        except lyrics.LyricsError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/songs/{slug}/lyrics")
    def del_lyrics(slug: str):
        lyrics.delete(get_song(slug))
        return {"ok": True}

    @app.get("/api/align/status")
    def align_status():
        return {**align.status(), "device": detect_device()["device"]}

    @app.post("/api/songs/{slug}/align")
    def start_align(slug: str, body: AlignBody):
        s = get_song(slug)
        if not lyrics.split_plain(body.text):
            raise HTTPException(400, "歌詞是空的：請貼上歌詞，一行一句。")
        try:
            align.vocals_for(s)
        except align.AlignError as e:
            raise HTTPException(400, str(e)) from e
        for j in ALIGN_JOBS.values():
            if j.state == "running":
                if j.slug == slug:
                    return j.public()
                raise HTTPException(409, "另一首歌正在對時間，請等它完成。")
        job = AlignJob(id=uuid.uuid4().hex[:10], slug=slug)
        ALIGN_JOBS[job.id] = job
        threading.Thread(target=_run_align, args=(job, s, body.text, body.lang), daemon=True).start()
        return job.public()

    @app.get("/api/align-jobs/{job_id}")
    def align_job(job_id: str):
        j = ALIGN_JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "找不到這個工作")
        return j.public()

    @app.post("/api/align-jobs/{job_id}/cancel")
    def align_cancel(job_id: str):
        j = ALIGN_JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "找不到這個工作")
        j._cancel.cancel()
        return j.public()

    # ---------------- 第 5 批：字幕樣式與輸出
    @app.get("/api/songs/{slug}/karaoke")
    def get_karaoke(slug: str):
        return karaoke_public(get_song(slug))

    @app.put("/api/songs/{slug}/karaoke")
    def put_karaoke(slug: str, body: dict):
        s = get_song(slug)
        kvideo.save_settings(s, body)
        return karaoke_public(s)

    @app.put("/api/songs/{slug}/bg-image")
    async def put_bg(slug: str, request: Request, name: str):
        s = get_song(slug)
        try:
            kvideo.save_bg_image(s, name, await request.body())
        except kvideo.KaraokeError as e:
            raise HTTPException(400, str(e)) from e
        return karaoke_public(s)

    def _file(p: Optional[Path]):
        if not p or not Path(p).exists():
            raise HTTPException(404, "沒有這個檔案")
        return FileResponse(p)

    @app.get("/api/songs/{slug}/bg-image")
    def bg_image(slug: str):
        return _file(kvideo.bg_image_path(get_song(slug)))

    @app.get("/api/songs/{slug}/cover")
    def cover(slug: str):
        return _file(kvideo.cover_path(get_song(slug)))

    @app.get("/api/songs/{slug}/video")
    def video(slug: str):
        s = get_song(slug)
        return _file(s.source_path if s.has_video else None)

    @app.post("/api/songs/{slug}/karaoke-render")
    def karaoke_render(slug: str, body: KaraokeRenderBody):
        s = get_song(slug)
        for j in KARAOKE_JOBS.values():
            if j.state == "running":
                if j.slug == slug:
                    return j.public()
                raise HTTPException(409, "另一首歌正在輸出，請等它完成。")
        lyr = lyrics.load(s)
        if not lyr or not lyr["lines"]:
            raise HTTPException(400, "這首歌還沒有歌詞。")
        job = KaraokeJob(id=uuid.uuid4().hex[:10], slug=slug)
        KARAOKE_JOBS[job.id] = job
        out_dir = body.outDir or load_settings().get("outDir") or None
        threading.Thread(target=_run_karaoke, args=(job, s, out_dir), daemon=True).start()
        return job.public()

    @app.get("/api/karaoke-jobs/{job_id}")
    def karaoke_job(job_id: str):
        j = KARAOKE_JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "找不到這個工作")
        return j.public()

    @app.post("/api/karaoke-jobs/{job_id}/cancel")
    def karaoke_cancel(job_id: str):
        j = KARAOKE_JOBS.get(job_id)
        if not j:
            raise HTTPException(404, "找不到這個工作")
        j._cancel.cancel()
        return j.public()

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    if UI_DIST.exists():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    return app


def serve(host: str = "127.0.0.1", port: int = 0):
    """在背景執行緒啟動後端，回傳實際的網址。port=0 = 自動找空的 port。"""
    import socket

    import uvicorn

    if port == 0:
        with socket.socket() as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host=host, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://{host}:{port}", server


if __name__ == "__main__":
    # 開發用：python -m karaoke.server 8765 → 用一般瀏覽器開
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
