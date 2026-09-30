"""漢字標假名（第 v0.7.0 批）：自動產生讀音，每句存成 ruby = [{s, e, r, m?}]。

- s、e：這段漢字在句子裡的位置（字元索引，e 不含）
- r：平假名讀音；空字串＝使用者設定「不標」
- m：使用者手改過（重新自動標音時保留）

讀音用 SudachiPy（離線字典，不用上網）。一句的 ruby 欄位：
- 沒有這個欄位（或 null）＝還沒產生
- []＝產生過，這句沒有要標的字
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional

KANJI = r"㐀-䶿一-鿿豈-﫿々〆ヶ"
_KANJI_RE = re.compile(f"[{KANJI}]+")

# 字典常見但歌詞裡通常不是這樣唱的讀音（整個詞完全相同才換）
OVERRIDES = {
    "私": "わたし",
    "明日": "あした",
    "何": "なに",
    "君": "きみ",
    "人": "ひと",
    "僕": "ぼく",
    "今日": "きょう",
    "一人": "ひとり",
    "二人": "ふたり",
}


class FuriganaError(Exception):
    pass


def has_kanji(text: str) -> bool:
    return bool(_KANJI_RE.search(text))


def kanji_runs(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _KANJI_RE.finditer(text)]


def available() -> bool:
    try:
        _tokenizer()
        return True
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=1)
def _tokenizer():
    from sudachipy import dictionary, tokenizer   # 延後載入：沒用到標音就不用花時間載字典

    tok = dictionary.Dictionary(dict="core").create()
    return tok, tokenizer.Tokenizer.SplitMode.A


def to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def _split_token(surface: str, reading: str) -> list[tuple[int, int, str]]:
    """一個詞（例如「灯り」＝あかり）→ 每段漢字的讀音 [(s, e, 讀音)]，位置以這個詞開頭為 0。

    用正規表示式對齊：漢字段 → (.+?)，假名 → 照字比對（片假名先轉平假名）。對不起來就整個詞標在漢字範圍上。
    """
    runs = kanji_runs(surface)
    if not runs:
        return []
    reading = to_hira(reading)
    if surface in OVERRIDES:
        reading = OVERRIDES[surface]
    parts, pos = [], 0
    for a, b in runs:
        if a > pos:
            parts.append(re.escape(to_hira(surface[pos:a])))
        parts.append("(.+?)")
        pos = b
    if pos < len(surface):
        parts.append(re.escape(to_hira(surface[pos:])))
    m = re.fullmatch("".join(parts), reading)
    if m:
        return [(a, b, g) for (a, b), g in zip(runs, m.groups())]
    # 對不起來（字典讀音跟字面不一致）：頭尾相同的假名去掉，剩下的標在第一個到最後一個漢字上
    a, b = runs[0][0], runs[-1][1]
    head, tail = to_hira(surface[:a]), to_hira(surface[b:])
    r = reading
    if head and r.startswith(head):
        r = r[len(head):]
    if tail and r.endswith(tail):
        r = r[: len(r) - len(tail)]
    return [(a, b, r)] if r else []


def auto_line(text: str) -> list[dict]:
    """一句歌詞 → 自動讀音 [{s, e, r}]。"""
    if not has_kanji(text):
        return []
    tok, mode = _tokenizer()
    out: list[dict] = []
    for m in tok.tokenize(text, mode):
        surface, start = m.surface(), m.begin()
        if not has_kanji(surface):
            continue
        reading = m.reading_form()
        if not reading or reading == surface:
            continue
        for a, b, r in _split_token(surface, reading):
            if r and r != surface[a:b]:
                out.append({"s": start + a, "e": start + b, "r": r})
    return out


def _overlaps(a: dict, b: dict) -> bool:
    return a["s"] < b["e"] and b["s"] < a["e"]


def fill(lines: list[dict], *, redo: bool = False) -> tuple[list[dict], int]:
    """補上還沒產生讀音的句子（redo＝全部重新產生，但保留手改過的）。回傳 (句子, 改了幾句)。"""
    changed = 0
    out = []
    for ln in lines:
        ln = dict(ln)
        cur = ln.get("ruby")
        if cur is None or redo:
            manual = [x for x in (cur or []) if x.get("m")]
            try:
                auto = [x for x in auto_line(ln["text"]) if not any(_overlaps(x, k) for k in manual)]
            except Exception as e:  # noqa: BLE001
                logging.exception("自動標音失敗：%s", ln["text"])
                raise FuriganaError(f"自動標音失敗：{e}") from e
            ruby = sorted(manual + auto, key=lambda x: x["s"])
            if ruby != cur:
                changed += 1
            ln["ruby"] = ruby
        out.append(ln)
    return out, changed


def clean_ruby(ruby, text: str) -> Optional[list[dict]]:
    """檢查存檔的讀音：位置在句子範圍內、不重疊、讀音不超過 20 字。不合格的項目丟掉；不是清單＝None（還沒產生）。"""
    if not isinstance(ruby, list):
        return None
    out: list[dict] = []
    for x in ruby:
        if not isinstance(x, dict):
            continue
        try:
            s, e = int(x.get("s")), int(x.get("e"))
        except (TypeError, ValueError):
            continue
        r = str(x.get("r") or "").strip()[:20]
        if not (0 <= s < e <= len(text)):
            continue
        item = {"s": s, "e": e, "r": r}
        if x.get("m"):
            item["m"] = True
        elif not r:
            continue
        if any(_overlaps(item, k) for k in out):
            continue
        out.append(item)
    return sorted(out, key=lambda x: x["s"])
