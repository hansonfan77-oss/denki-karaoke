"""卡拉影片的歌詞（第 4 批）：LRCLIB 搜尋、LRC 解析／輸出、每首歌的歌詞存檔。

歌詞存在 songs/<slug>/lyrics.json（清除中間檔時不會刪）：
  {
    "version": 1,
    "source": "lrclib" | "ai" | "lrc" | "manual",
    "lang": "ja",
    "offset": 1.2,              ← 整體前後移（秒，正數＝字幕延後）
    "lines": [{"t": 21.4, "end": 26.1, "text": "…"}, …],   ← t / end 是「還沒加 offset」的原始時間
    "lrclib": {"id": 123, "trackName": "…", "artistName": "…"},
    "updated_at": "2026-09-30T05:00:00"
  }
實際顯示的時間 = t + offset。end 可以是 null（LRC 只有開始時間），這時用下一句的開始時間當結束。
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import __version__, config
from .project import Song, now_iso

LRCLIB_API = os.environ.get("DENKI_LRCLIB_API", "https://lrclib.net/api").rstrip("/")
USER_AGENT = f"DENKI-karaoke/{__version__} (https://github.com/{config.GITHUB_REPO})"
SOURCES = ("lrclib", "ai", "lrc", "manual")
OFFSET_LIMIT = 60.0


class LyricsError(RuntimeError):
    pass


# ------------------------------------------------------------------ LRC
_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_WORD_TAG = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_OFFSET_TAG = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]", re.I)


def _tag_seconds(mm: str, ss: str) -> float:
    ss = ss.replace(":", ".")
    return int(mm) * 60 + float(ss)


def parse_lrc(text: str) -> list[dict]:
    """LRC 文字 → [{t, end, text}]（依時間排序）。

    - 一行可以有好幾個時間標籤（副歌重複）：[00:12.00][01:05.30]歌詞
    - 逐字標籤 <mm:ss.xx> 先去掉（第一版只做逐句）
    - 空白的時間標籤（常見於 LRCLIB：間奏開始）→ 當成上一句的結束時間
    - [offset:+500]（毫秒，正數＝歌詞提早）照 LRC 慣例套用
    """
    offset_ms = 0
    entries: list[tuple[float, str]] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip().lstrip("﻿")
        m = _OFFSET_TAG.match(line)
        if m:
            offset_ms = int(m.group(1))
            continue
        stamps = []
        pos = 0
        while True:
            m = _TIME_TAG.match(line, pos)
            if not m:
                break
            stamps.append(_tag_seconds(m.group(1), m.group(2)))
            pos = m.end()
        if not stamps:
            continue   # [ar:…] [ti:…] 之類的資訊，或沒有時間的文字
        lyric = _WORD_TAG.sub("", line[pos:]).strip()
        for t in stamps:
            entries.append((t, lyric))
    entries.sort(key=lambda e: e[0])
    shift = -offset_ms / 1000.0
    out: list[dict] = []
    for t, lyric in entries:
        t = max(0.0, round(t + shift, 3))
        if not lyric:
            if out and out[-1]["end"] is None:
                out[-1]["end"] = t
            continue
        out.append({"t": t, "end": None, "text": lyric})
    return out


def _fmt_tag(sec: float) -> str:
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    return f"[{cs // 6000:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}]"


def format_lrc(lines: list[dict], offset: float = 0.0, title: str = "", artist: str = "") -> str:
    """[{t, end, text}] → LRC 文字（時間已經加上 offset）。句子之間有空檔時補一個空白標籤，
    這樣再匯入時結束時間不會跑掉。"""
    out = []
    if title:
        out.append(f"[ti:{title}]")
    if artist:
        out.append(f"[ar:{artist}]")
    out.append(f"[re:DENKI 伴奏工具 {__version__}]")
    items = sorted(lines, key=lambda x: x["t"])
    for i, ln in enumerate(items):
        out.append(f"{_fmt_tag(ln['t'] + offset)}{ln['text']}")
        end = ln.get("end")
        nxt = items[i + 1]["t"] if i + 1 < len(items) else None
        if end is not None and (nxt is None or nxt - end > 0.3):
            out.append(_fmt_tag(end + offset))
    return "\n".join(out) + "\n"


def split_plain(text: str) -> list[str]:
    """貼上的純文字 → 一句一行（去掉空行、LRC 時間標籤、[Chorus] 之類的段落標記）。"""
    out = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = _WORD_TAG.sub("", _TIME_TAG.sub("", raw)).strip()
        if not line or re.fullmatch(r"[\[(（【].{0,20}[\])）】]", line):
            continue
        out.append(line)
    return out


# ------------------------------------------------------------------ 語言、歌名
def detect_language(text: str) -> str:
    """看字元判斷語言（AI 對時間要指定）：有假名＝日文、有諺文＝韓文、有漢字沒假名＝中文，其他當英文。"""
    kana = hangul = han = latin = 0
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9F:
            kana += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
            hangul += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            han += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    if kana >= 3 or (kana and kana * 10 >= han):
        return "ja"
    if hangul >= 3:
        return "ko"
    if han >= 3:
        return "zh"
    return "en"


LANG_LABEL = {"ja": "日文", "zh": "中文", "ko": "韓文", "en": "英文"}


def guess_title(name: str) -> str:
    """從檔名猜歌名：『』「」裡面的字優先；否則去掉【】[] () 的註記和開頭的曲目編號。"""
    for pat in (r"『([^』]+)』", r"「([^」]+)」"):
        m = re.search(pat, name)
        if m and m.group(1).strip():
            return m.group(1).strip()
    s = re.sub(r"【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\)", " ", name)
    s = re.sub(r"[【\[（(].*$", "", s)          # 沒有關起來的括號：後面都是註記
    s = re.sub(r"^\s*\d{1,3}\s*[、.．\-_ ]\s*", "", s)
    s = re.split(r"[｜|/／]", s)[0]
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip(" -–—")
    return s or name.strip()


# ------------------------------------------------------------------ LRCLIB
def _get_json(url: str, timeout: float = 12.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — 固定的 https 網址
        return json.loads(r.read().decode("utf-8"))


def _result(item: dict, duration: Optional[float]) -> dict:
    synced = item.get("syncedLyrics") or ""
    plain = item.get("plainLyrics") or ""
    lines = parse_lrc(synced) if synced else []
    dur = item.get("duration")
    diff = abs(float(dur) - duration) if (dur and duration) else None
    return {
        "id": item.get("id"),
        "trackName": item.get("trackName") or item.get("name") or "",
        "artistName": item.get("artistName") or "",
        "albumName": item.get("albumName") or "",
        "duration": dur,
        "durationDiff": round(diff, 1) if diff is not None else None,
        "instrumental": bool(item.get("instrumental")),
        "synced": bool(lines),
        "lines": lines,
        "plain": plain or "\n".join(ln["text"] for ln in lines),
    }


def search_lrclib(title: str, artist: str = "", duration: Optional[float] = None, limit: int = 20) -> list[dict]:
    """搜尋 LRCLIB。排序：附時間的在前，再依時長差距。連不上網路時丟 LyricsError（訊息可以直接顯示）。"""
    title, artist = title.strip(), artist.strip()
    if not title:
        raise LyricsError("請輸入歌名")
    queries = []
    q = {"track_name": title}
    if artist:
        q["artist_name"] = artist
    queries.append(q)
    queries.append({"q": f"{title} {artist}".strip()})
    items: list[dict] = []
    seen: set = set()
    try:
        for q in queries:
            data = _get_json(f"{LRCLIB_API}/search?{urllib.parse.urlencode(q)}")
            for it in data if isinstance(data, list) else []:
                if it.get("id") in seen:
                    continue
                seen.add(it.get("id"))
                items.append(it)
            if items:
                break
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        logging.warning("LRCLIB 搜尋失敗：%s", e)
        raise LyricsError("連不上 LRCLIB（網路或網站暫時有問題）。可以稍後再試，或改用「貼上歌詞」。") from e
    results = [_result(it, duration) for it in items if not it.get("instrumental")]
    results.sort(key=lambda r: (not r["synced"], r["durationDiff"] if r["durationDiff"] is not None else 9999))
    return results[:limit]


# ------------------------------------------------------------------ 存檔
def lyrics_path(song: Song):
    return song.dir / "lyrics.json"


def clean_lines(lines: list[dict]) -> list[dict]:
    out = []
    for ln in lines:
        text = str(ln.get("text", "")).strip()
        if not text:
            continue
        try:
            t = max(0.0, round(float(ln.get("t", 0.0)), 3))
        except (TypeError, ValueError):
            continue
        end = ln.get("end")
        try:
            end = round(float(end), 3) if end is not None else None
        except (TypeError, ValueError):
            end = None
        if end is not None and end <= t:
            end = None
        out.append({"t": t, "end": end, "text": text[:200]})
    out.sort(key=lambda x: x["t"])
    return out


def load(song: Song) -> Optional[dict]:
    p = lyrics_path(song)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["lines"] = clean_lines(data.get("lines", []))
    data.setdefault("offset", 0.0)
    data.setdefault("lang", detect_language(" ".join(ln["text"] for ln in data["lines"])))
    return data


def save(song: Song, lines: list[dict], *, source: str, offset: float = 0.0,
         lang: Optional[str] = None, lrclib: Optional[dict] = None) -> dict:
    if source not in SOURCES:
        raise LyricsError(f"不認得的歌詞來源：{source}")
    lines = clean_lines(lines)
    if not lines:
        raise LyricsError("歌詞是空的")
    data = {
        "version": 1,
        "source": source,
        "lang": lang or detect_language(" ".join(ln["text"] for ln in lines)),
        "offset": round(max(-OFFSET_LIMIT, min(OFFSET_LIMIT, float(offset))), 3),
        "lines": lines,
        "lrclib": lrclib or None,
        "updated_at": now_iso(),
    }
    song.dir.mkdir(parents=True, exist_ok=True)
    p = lyrics_path(song)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return data


def delete(song: Song) -> None:
    lyrics_path(song).unlink(missing_ok=True)


# ------------------------------------------------------------------ 字元對應（AI 對時間用）
def norm_chars(s: str) -> str:
    """只留字母、數字、漢字、假名（標點、空白都去掉），全形轉半形、英文轉小寫。"""
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(ch for ch in s if unicodedata.category(ch)[0] in ("L", "N"))


def map_words_to_lines(words: list[dict], lines: list[str]) -> list[dict]:
    """AI 回傳的逐字（詞）時間 → 每一句的開始／結束。

    把兩邊都攤平成「字元串」後依順序對應，不依賴 AI 怎麼斷句。兩邊字數對不上時（極少數：
    斷詞器改了字），用比例換算位置，仍然保持順序。
    沒有可對應字元的句子（例如只有「…」）沿用上一句的結束時間。
    """
    stream: list[tuple[float, float]] = []
    for w in words:
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        for _ in norm_chars(str(w.get("text", ""))):
            stream.append((ws, we))
    lens = [len(norm_chars(ln)) for ln in lines]
    total = sum(lens)
    out: list[dict] = []
    if not stream or not total:
        return [{"t": 0.0, "end": None, "text": ln} for ln in lines]
    scale = len(stream) / total
    pos, prev_end = 0, 0.0
    for text, n in zip(lines, lens):
        if n == 0:
            out.append({"t": round(prev_end, 3), "end": None, "text": text})
            continue
        a = min(len(stream) - 1, int(round(pos * scale)))
        b = min(len(stream) - 1, max(a, int(round((pos + n) * scale)) - 1))
        start, end = stream[a][0], stream[b][1]
        if end <= start:
            end = start + 0.5
        out.append({"t": round(start, 3), "end": round(end, 3), "text": text})
        prev_end = end
        pos += n
    return out
