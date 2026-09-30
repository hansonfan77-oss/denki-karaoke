"""卡拉影片（第 5 批）：字幕樣式、背景 → 產生 ASS 字幕 → FFmpeg 燒進 1080p MP4。

字幕的顯示規則跟介面預覽（ui/src/karaoke/lyricsView.ts）是同一套，改一邊一定要改另一邊：
- 兩行交替：第 0、2、4… 句在上面（靠左），第 1、3、5… 句在下面（靠右）。
- 提前顯示：一句在「上一句開始唱」時出現（最多提前 LEAD_MAX 秒）。
- 間奏（空檔 ≥ GAP 秒）：唱完 HOLD 秒後清空；下一句提前 LEAD_AFTER_GAP 秒出現，前 3 秒顯示 ●●● 倒數。
- 掃色：整句均分（ASS 的 \\kf）；關掉時整句在開始唱的瞬間變色。
- 底板：有字幕（或倒數）的時候才出現，整條橫跨畫面、半透明。
版面數字（字高、行距、邊界）也跟預覽一樣，都以 1920×1080 為準。

每首歌的設定存在 songs/<slug>/karaoke.json；最後一次用的字幕樣式另外記在 settings.json（下一首歌沿用）。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

from . import config, export, ffmpeg, lyrics, mix
from .project import Song, slugify

W, H = 1920, 1080
FPS = 30

LEAD_MAX = 8
GAP = 5
LEAD_AFTER_GAP = 4
HOLD = 1.2
COUNTDOWN = 3

FONTS: dict[str, dict] = {
    "jhenghei": {"label": "微軟正黑體", "family": "Microsoft JhengHei", "files": ["msjhbd.ttc", "msjh.ttc"]},
    "yugothic": {"label": "遊ゴシック（日文）", "family": "Yu Gothic", "files": ["YuGothB.ttc", "YuGothM.ttc", "YuGothR.ttc"]},
    "meiryo": {"label": "メイリオ（日文）", "family": "Meiryo", "files": ["meiryob.ttc", "meiryo.ttc"]},
    "mingliu": {"label": "新細明體", "family": "PMingLiU", "files": ["mingliu.ttc"]},
}

DEFAULT_STYLE = {
    "font": "jhenghei",
    "size": 64,              # 1080p 畫面上的字高（px）
    "unsung": "#FFFFFF",
    "sung": "#4FD1C5",
    "outline": 3,            # 描邊粗細（px）
    "position": "bottom",    # bottom / middle
    "plate": "black",        # none / black / white
    "plateOpacity": 50,      # 0~100
    "countdown": True,
    "sweep": True,
    "sweepWord": True,       # 有 AI 逐字時間的句子用逐字掃色（沒有的句子自動用整句均分）
}
DEFAULT_BG = {"kind": "auto", "color": "#1F2D36", "fit": "fill"}   # kind: auto / video / color / image / cover
BG_FITS = ("fill", "fit", "center")   # 自選圖片：填滿（裁切）/ 完整顯示 / 置中（原尺寸），空白處用模糊的同一張圖補
BG_KINDS = ("auto", "video", "color", "image", "cover")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

Progress = Callable[[float, str], None]


class KaraokeError(RuntimeError):
    pass


class Cancelled(KaraokeError):
    pass


# ------------------------------------------------------------------ 設定
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _color(v, default: str) -> str:
    return v.upper() if isinstance(v, str) and _HEX.match(v) else default


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def normalize_style(d: Optional[dict]) -> dict:
    d = d or {}
    s = dict(DEFAULT_STYLE)
    s["font"] = d.get("font") if d.get("font") in FONTS else s["font"]
    s["size"] = int(_num(d.get("size"), 36, 110, s["size"]))
    s["unsung"] = _color(d.get("unsung"), s["unsung"])
    s["sung"] = _color(d.get("sung"), s["sung"])
    s["outline"] = int(_num(d.get("outline"), 0, 8, s["outline"]))
    s["position"] = d.get("position") if d.get("position") in ("bottom", "middle") else s["position"]
    s["plate"] = d.get("plate") if d.get("plate") in ("none", "black", "white") else s["plate"]
    s["plateOpacity"] = int(_num(d.get("plateOpacity"), 0, 100, s["plateOpacity"]))
    s["countdown"] = bool(d.get("countdown", s["countdown"]))
    s["sweep"] = bool(d.get("sweep", s["sweep"]))
    s["sweepWord"] = bool(d.get("sweepWord", s["sweepWord"]))
    return s


def normalize_bg(d: Optional[dict]) -> dict:
    d = d or {}
    return {"kind": d.get("kind") if d.get("kind") in BG_KINDS else "auto",
            "color": _color(d.get("color"), DEFAULT_BG["color"]),
            "fit": d.get("fit") if d.get("fit") in BG_FITS else "fill"}


def _settings_path() -> Path:
    return config.ROOT / "settings.json"


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def default_audio(song: Song) -> dict:
    """伴奏設定預設值：這首歌最後一次在「伴奏處理」輸出的 Key、導唱、模式。"""
    last = next((r for r in reversed(song.meta.get("renders", [])) if r.get("kind") != "karaoke"), None)
    mode = (last or {}).get("mode") or song.default_mode() or "standard"
    if not song.is_analyzed(mode):
        mode = song.default_mode() or mode
    return {"mode": mode, "key": int((last or {}).get("key", 0)), "guide": int((last or {}).get("guide", 0))}


def load_settings(song: Song) -> dict:
    data = _read_json(song.dir / "karaoke.json")
    glob = _read_json(_settings_path()).get("karaokeStyle")
    audio = {**default_audio(song), **{k: v for k, v in (data.get("audio") or {}).items() if k in ("mode", "key", "guide")}}
    if not song.is_analyzed(audio["mode"]):
        audio["mode"] = song.default_mode() or audio["mode"]
    return {
        "style": normalize_style(data.get("style") or glob),
        "background": normalize_bg(data.get("background")),
        "audio": audio,
        "alsoLrc": bool(data.get("alsoLrc", True)),
    }


def save_settings(song: Song, body: dict) -> dict:
    cur = load_settings(song)
    style = normalize_style({**cur["style"], **(body.get("style") or {})})
    bg = normalize_bg({**cur["background"], **(body.get("background") or {})})
    audio = dict(cur["audio"])
    a = body.get("audio") or {}
    if a.get("mode") in config.MODES:
        audio["mode"] = a["mode"]
    if "key" in a:
        audio["key"] = int(_num(a["key"], config.KEY_MIN, config.KEY_MAX, audio["key"]))
    if "guide" in a:
        audio["guide"] = int(_num(a["guide"], config.GUIDE_MIN, config.GUIDE_MAX, audio["guide"]))
    also_lrc = bool(body.get("alsoLrc", cur["alsoLrc"]))
    _write_json(song.dir / "karaoke.json", {"style": style, "background": bg, "audio": audio, "alsoLrc": also_lrc})
    glob = _read_json(_settings_path())
    glob["karaokeStyle"] = style
    _write_json(_settings_path(), glob)
    return load_settings(song)


# ------------------------------------------------------------------ 背景
def bg_image_path(song: Song) -> Optional[Path]:
    for ext in IMAGE_EXTS:
        p = song.dir / f"background{ext}"
        if p.exists():
            return p
    return None


def save_bg_image(song: Song, name: str, data: bytes) -> Path:
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXTS:
        raise KaraokeError("背景圖片只支援 JPG、PNG、WebP、BMP。")
    if len(data) > 40_000_000:
        raise KaraokeError("圖片太大（超過 40 MB）。")
    old = bg_image_path(song)
    if old:
        old.unlink(missing_ok=True)
    p = song.dir / f"background{ext}"
    p.write_bytes(data)
    return p


def cover_path(song: Song) -> Optional[Path]:
    """音檔內嵌的專輯封面（第一次抽出來存成 cover.png；沒有封面回傳 None）。"""
    p = song.dir / "cover.png"
    miss = song.dir / "cover.none"
    if p.exists():
        return p
    if miss.exists() or song.has_video or not song.source_path.exists():
        return None
    try:
        ffmpeg.run(["-i", song.source_path, "-an", "-map", "0:v:0", "-frames:v", "1", p])
        if p.exists() and p.stat().st_size > 0:
            return p
    except ffmpeg.FFmpegError:
        pass
    p.unlink(missing_ok=True)
    try:
        miss.write_text("", encoding="utf-8")
    except OSError:
        pass
    return None


def resolve_bg(song: Song, bg: dict) -> dict:
    """auto → 影片用原畫面，音檔用模糊封面，都沒有就單色。回傳實際用的 {kind, color, path?}。"""
    kind = bg["kind"]
    if kind == "auto":
        kind = "video" if song.has_video else ("cover" if cover_path(song) else "color")
    if kind == "video" and not (song.has_video and song.source_path.exists()):
        kind = "cover" if cover_path(song) else "color"
    if kind == "cover" and not cover_path(song):
        kind = "color"
    if kind == "image" and not bg_image_path(song):
        kind = "color"
    path = {"video": song.source_path, "cover": cover_path(song), "image": bg_image_path(song)}.get(kind)
    return {"kind": kind, "color": bg["color"], "fit": bg.get("fit", "fill"), "path": str(path) if path else None}


# ------------------------------------------------------------------ 版面（跟預覽共用的數字）
def geometry(style: dict) -> dict:
    fs = style["size"]
    pad_v, pad_h = fs * 0.45, max(60.0, fs * 1.1)
    dots_h, line_h, gap = fs * 0.5, fs * 1.25, fs * 0.25
    plate_h = 2 * pad_v + 2 * (dots_h + line_h) + gap
    top = H * 0.96 - plate_h if style["position"] == "bottom" else (H - plate_h) / 2
    slots = []
    for s in (0, 1):
        y0 = top + pad_v + s * (dots_h + line_h + gap)
        slots.append({"dots": y0, "line": y0 + dots_h})
    return {"fs": fs, "padH": pad_h, "plateTop": top, "plateH": plate_h, "lineH": line_h, "slots": slots}


def text_width(text: str, fs: float) -> float:
    w = 0.0
    for ch in text:
        if ch == " ":
            w += 0.3
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 1.0
        else:
            w += 0.58
    return w * fs


def fit_size(text: str, fs: float, pad_h: float) -> float:
    """太長的句子縮小，不要超出畫面。"""
    room = W - 2 * pad_h
    w = text_width(text, fs)
    return fs if w <= room else max(fs * 0.5, fs * room / w)


# ------------------------------------------------------------------ 顯示規則（同 lyricsView.ts）
def timed(lines: list[dict], offset: float) -> list[dict]:
    out = []
    for i, ln in enumerate(lines):
        t = ln["t"] + offset
        nxt = lines[i + 1]["t"] + offset if i + 1 < len(lines) else math.inf
        end = ln["end"] + offset if ln.get("end") is not None else min(nxt - 0.05, t + 6)
        if not end > t:
            end = t + 0.5
        words = [{**w, "t": w["t"] + offset, "end": w["end"] + offset} for w in ln["words"]] if ln.get("words") else None
        out.append({"t": t, "end": end, "text": ln["text"], "words": words})
    return out


def appear(L: list[dict], j: int) -> float:
    cur = L[j]
    if j == 0:
        return cur["t"] - LEAD_AFTER_GAP
    prev = L[j - 1]
    if cur["t"] - prev["end"] >= GAP:
        return cur["t"] - LEAD_AFTER_GAP
    return max(prev["t"], cur["t"] - LEAD_MAX)


def vanish(L: list[dict], j: int) -> float:
    cur = L[j]
    nxt = L[j + 1] if j + 1 < len(L) else None
    if nxt is None:
        return cur["end"] + HOLD + 1
    if nxt["t"] - cur["end"] >= GAP:
        return cur["end"] + HOLD
    after = L[j + 2] if j + 2 < len(L) else None
    if after is None or after["t"] - nxt["end"] >= GAP:
        return max(cur["end"], nxt["end"] + HOLD)
    return max(appear(L, j + 2), cur["end"])


def countdowns(L: list[dict]) -> list[tuple[int, float]]:
    """需要倒數的句子：(句子編號, 開始時間)。"""
    out = []
    for j, ln in enumerate(L):
        gap_before = ln["t"] if j == 0 else ln["t"] - L[j - 1]["end"]
        if gap_before >= GAP - 0.01:
            out.append((j, ln["t"]))
    return out


# ------------------------------------------------------------------ ASS 字幕
def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ")


def build_ass(lines: list[dict], offset: float, style: dict, duration: float) -> str:
    s = normalize_style(style)
    g = geometry(s)
    fs, pad_h = g["fs"], g["padH"]
    family = FONTS[s["font"]]["family"]
    L = timed(lines, offset)
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        # 卡拉OK：PrimaryColour = 唱過的顏色、SecondaryColour = 還沒唱的顏色
        f"Style: Lyric,{family},{fs:.0f},{_ass_color(s['sung'])},{_ass_color(s['unsung'])},&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{s['outline']},0,7,0,0,0,1",
        f"Style: Dots,{family},{fs * 0.42:.0f},&H0066D1FF,&H0066D1FF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,6,0,1,{max(1, s['outline'] - 1)},0,7,0,0,0,1",
        f"Style: Plate,{family},10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev: list[str] = []
    spans: list[tuple[float, float]] = []

    for j, ln in enumerate(L):
        a, v = max(0.0, appear(L, j)), min(duration, vanish(L, j))
        if v <= a:
            continue
        spans.append((a, v))
        slot = j % 2
        y = g["slots"][slot]["line"]
        size = fit_size(ln["text"], fs, pad_h)
        y += (g["lineH"] - size * 1.25) / 2        # 縮小的句子垂直置中在原本的行高裡
        if slot == 0:
            pos = f"\\an7\\pos({pad_h:.0f},{y:.0f})"
        else:
            pos = f"\\an9\\pos({W - pad_h:.0f},{y:.0f})"
        fs_tag = f"\\fs{size:.0f}" if abs(size - fs) > 0.5 else ""
        ev.append(f"Dialogue: 2,{_ts(a)},{_ts(v)},Lyric,,0,0,0,,{{{pos}{fs_tag}}}{_karaoke(ln, a, s)}")

    if s["countdown"]:
        for j, t in countdowns(L):
            slot = j % 2
            y = g["slots"][slot]["dots"]
            pos = f"\\an7\\pos({pad_h:.0f},{y:.0f})" if slot == 0 else f"\\an9\\pos({W - pad_h:.0f},{y:.0f})"
            for k in (3, 2, 1):
                st, en = t - k, t - k + 1
                if st < 0:
                    continue
                spans.append((st, en))
                ev.append(f"Dialogue: 1,{_ts(st)},{_ts(en)},Dots,,0,0,0,,{{{pos}}}{'●' * k + '○' * (3 - k)}")

    if s["plate"] != "none" and spans:
        color = "#000000" if s["plate"] == "black" else "#FFFFFF"
        alpha = int(round(255 * (1 - s["plateOpacity"] / 100)))
        draw = f"m 0 0 l {W} 0 {W} {g['plateH']:.0f} 0 {g['plateH']:.0f}"
        for a, v in _merge(spans):
            ev.append(f"Dialogue: 0,{_ts(a)},{_ts(min(duration, v))},Plate,,0,0,0,,"
                      f"{{\\an7\\pos(0,{g['plateTop']:.0f})\\1c&H{color[5:7]}{color[3:5]}{color[1:3]}&\\1a&H{alpha:02X}&\\bord0\\shad0\\p1}}{draw}")

    return "\n".join(head + ev) + "\n"


def _karaoke(ln: dict, a: float, s: dict) -> str:
    """一句的卡拉OK 標籤。時間單位是百分之一秒，從字幕出現（a）開始算。
    逐字：每一段各自一個 \\kf，段與段之間的空檔用沒有字的 \\k 補上；整句：一個 \\kf 從頭掃到尾；不掃色：開始唱時整句一次變色。"""
    cs = lambda x: max(0, int(round(x * 100)))  # noqa: E731
    if not s["sweep"]:
        return f"{{\\k{cs(ln['t'] - a)}}}{{\\k1}}{_esc(ln['text'])}"
    words = ln.get("words") if s.get("sweepWord", True) else None
    if not words:
        return f"{{\\k{cs(ln['t'] - a)}}}{{\\kf{max(1, cs(ln['end'] - ln['t']))}}}{_esc(ln['text'])}"
    out, clock = [], 0      # clock：目前累計到哪（百分之一秒，從 a 算）
    for w in words:
        st, en = cs(w["t"] - a), cs(w["end"] - a)
        if st > clock:
            out.append(f"{{\\k{st - clock}}}")
            clock = st
        dur = max(1, en - clock)
        out.append(f"{{\\kf{dur}}}{_esc(w['text'])}")
        clock += dur
    return "".join(out)


def _merge(spans: list[tuple[float, float]], join: float = 0.4) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, v in sorted(spans):
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], v)
        else:
            out.append([a, v])
    return [(a, v) for a, v in out]


# ------------------------------------------------------------------ 輸出
def output_name(song: Song, key: int, guide: int) -> str:
    parts = [slugify(song.title), "卡拉", export.key_label(key)]
    if guide > 0:
        parts.append(f"導唱{guide}")
    return "_".join(parts) + ".mp4"


def _font_dir(tmp: Path, font: str) -> Path:
    """把要用的字型複製到暫存資料夾給 libass 用（不用整個 Windows\\Fonts，載入快、也不怕抓錯字型）。"""
    d = tmp / "fonts"
    d.mkdir(exist_ok=True)
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for key in dict.fromkeys([font, "jhenghei"]):
        for f in FONTS[key]["files"]:
            src = win_fonts / f
            if src.exists():
                try:
                    shutil.copy2(src, d / f)
                except OSError:
                    pass
    return d


_ENCODERS: Optional[str] = None


def _encoders() -> str:
    global _ENCODERS
    if _ENCODERS is None:
        from .proc import NO_WINDOW
        p = subprocess.run([ffmpeg.find("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **NO_WINDOW)
        _ENCODERS = p.stdout or ""
    return _ENCODERS


def video_codecs(still: bool) -> list[list[str]]:
    """先試顯卡編碼（NVENC，快很多），失敗就用 CPU（libx264）。"""
    from .deps import has_nvidia
    out = []
    if has_nvidia() and "h264_nvenc" in _encoders() and os.environ.get("DENKI_NO_NVENC") != "1":
        out.append(["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"])
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if still:
        x264 += ["-tune", "stillimage"]
    out.append(x264)
    return out


def _bg_input(bg: dict, duration: float) -> tuple[list[str], str]:
    """回傳 (輸入參數, 把第 0 個輸入變成 1920×1080 畫面的濾鏡)。"""
    fill = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if bg["kind"] == "video":
        return (["-i", bg["path"]],
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={FPS}")
    if bg["kind"] in ("image", "cover"):
        inp = ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.2f}", "-i", bg["path"]]
        blur = "boxblur=40:2,colorchannelmixer=rr=0.7:gg=0.7:bb=0.7"
        fit = bg.get("fit", "fill") if bg["kind"] == "image" else "fill"
        if bg["kind"] == "cover":
            return inp, f"[0:v]{fill},setsar=1,{blur}"
        if fit == "fill":
            return inp, f"[0:v]{fill},setsar=1"
        # 完整顯示／置中：底下鋪一張模糊放大的同一張圖，上面放完整的圖（比例不變）
        fg = (f"scale={W}:{H}:force_original_aspect_ratio=decrease" if fit == "fit"
              else f"scale='min(iw,{W})':'min(ih,{H})':force_original_aspect_ratio=decrease")
        return inp, (f"[0:v]split[bga][bgb];[bga]{fill},setsar=1,{blur}[bgblur];[bgb]{fg},setsar=1[bgfg];"
                     f"[bgblur][bgfg]overlay=(W-w)/2:(H-h)/2")
    c = bg["color"][1:]
    return (["-f", "lavfi", "-t", f"{duration:.2f}", "-i", f"color=c=0x{c}:s={W}x{H}:r={FPS}"], "[0:v]setsar=1")


def _run_ffmpeg(cmd: list[str], cwd: Path, duration: float, progress: Progress,
                cancel: Optional["CancelToken"]) -> tuple[int, str]:
    from .proc import NO_WINDOW
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, **NO_WINDOW)
    if cancel:
        cancel.proc = proc
    err: list[str] = []

    def read_err() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            err.append(raw.decode("utf-8", "replace").rstrip())
            del err[:-20]

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                sec = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            progress(min(99.0, 5 + sec / max(duration, 0.1) * 94), "合成畫面與字幕")
    code = proc.wait()
    t.join(timeout=2)
    return code, "\n".join(err[-12:])


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


def render(song: Song, *, out_dir: Optional[Path] = None, also_lrc: Optional[bool] = None,
           progress: Optional[Progress] = None, cancel: Optional[CancelToken] = None) -> dict:
    """輸出卡拉影片。回傳 {files: [...], seconds, encoder}。"""
    progress = progress or (lambda *_: None)
    lyr = lyrics.load(song)
    if not lyr or not lyr["lines"]:
        raise KaraokeError("這首歌還沒有歌詞，請先完成「選歌與歌詞」和「對時間」。")
    st = load_settings(song)
    audio, style = st["audio"], st["style"]
    also_lrc = st["alsoLrc"] if also_lrc is None else also_lrc
    mode = audio["mode"]
    if not song.is_analyzed(mode):
        raise KaraokeError("這首歌的中間檔不見了，請到「伴奏處理」重新分析。")

    from . import pipeline
    out_dir = Path(out_dir) if out_dir else song.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / output_name(song, audio["key"], audio["guide"])
    t0 = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="denki-kv-") as tmp_s:
        tmp = Path(tmp_s)
        progress(1, "混音（伴奏、Key、導唱）")
        gain = pipeline.effective_compensation(song, mode, audio["guide"], True)
        if gain:
            pipeline.ensure_loudness(song)
            gain = pipeline.effective_compensation(song, mode, audio["guide"], True)
        wav = tmp / "audio.wav"
        mix.render_audio(song.stem(mode, "no_vocals"), song.stem(mode, "vocals"), wav,
                         key=audio["key"], guide=audio["guide"], tempo=100, gain_db=gain)
        if cancel and cancel.cancelled:
            raise Cancelled("已取消")
        duration = float(ffmpeg.probe(wav).get("duration") or song.meta.get("duration") or 0)
        (tmp / "k.ass").write_text(build_ass(lyr["lines"], lyr["offset"], style, duration), encoding="utf-8-sig")
        _font_dir(tmp, style["font"])
        bg = resolve_bg(song, st["background"])
        progress(4, "準備背景")
        bg_args, chain = _bg_input(bg, duration)
        vf = f"{chain},format=yuv420p,ass=k.ass:fontsdir=fonts[v]"
        still = bg["kind"] != "video"
        tail = ["-map", "[v]", "-map", "1:a:0", "-c:a", "aac", "-b:a", config.VIDEO_AUDIO_BITRATE,
                "-pix_fmt", "yuv420p", "-r", str(FPS), "-shortest", "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", "out.mp4"]
        last_err, used = "", ""
        for codec in video_codecs(still):
            cmd = [ffmpeg.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *bg_args, "-i", "audio.wav",
                   "-filter_complex", vf, *codec, *tail]
            code, last_err = _run_ffmpeg(cmd, tmp, duration, progress, cancel)
            if cancel and cancel.cancelled:
                raise Cancelled("已取消")
            if code == 0 and (tmp / "out.mp4").exists():
                used = codec[1]
                break
            logging.warning("卡拉影片編碼失敗（%s）：%s", codec[1], last_err)
        else:
            raise KaraokeError(f"影片輸出失敗：{last_err[-600:]}")
        progress(99.5, "存檔")
        shutil.move(str(tmp / "out.mp4"), out)

    files = [out]
    if also_lrc:
        lrc = out.with_suffix(".lrc")
        lrc.write_text(lyrics.format_lrc(lyr["lines"], lyr["offset"], title=song.title), encoding="utf-8-sig")
        files.append(lrc)
    secs = round(time.perf_counter() - t0, 1)
    song.meta["last_mode"] = mode
    song.add_render({"kind": "karaoke", "mode": mode, "key": audio["key"], "guide": audio["guide"], "tempo": 100,
                     "gain_db": gain, "countin": 0, "format": "mp4", "file": str(out), "background": bg["kind"],
                     "encoder": used})
    logging.info("卡拉影片：%s，%s，%.1f 秒，背景 %s", out, used, secs, bg["kind"])
    progress(100, "完成")
    return {"files": [str(f) for f in files], "seconds": secs, "encoder": used, "background": bg["kind"]}
