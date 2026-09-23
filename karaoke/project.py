"""每首歌一個資料夾 + project.json。

這是「分析一次、之後秒出」的基礎：AI 分離只做一次，結果留在 stems/，
之後改 Key、改導唱、換模式比較都只是重新混音。

songs/<slug>/
  project.json
  source.wav                   ← 從原檔抽出的工作音軌（44.1k 立體聲），各模式共用
  stems/<模式>/no_vocals.flac  ← 伴奏（模式：standard / hq / harmony）
  stems/<模式>/vocals.flac     ← 人聲（保留和聲模式下只有主唱）
  out/                         ← 輸出檔（清除中間檔時不會動到）

舊版（第 1、2 批）的 stems/no_vocals.wav 會自動當成「標準」模式，不用重跑。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
STEM_NAMES = ("no_vocals", "vocals")


def slugify(name: str, max_len: int = 80) -> str:
    """做成 Windows 安全的資料夾名稱。保留中日文與空白，只去掉 Windows 不允許的字元。"""
    s = _BAD_CHARS.sub("_", name).strip().rstrip(". ")
    s = re.sub(r"\s+", " ", s)[:max_len].rstrip(". ")
    if not s or s.upper() in _RESERVED:
        s = f"song_{s or 'untitled'}"
    return s


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


@dataclass
class Song:
    dir: Path
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._migrate()

    # ---------- 路徑
    @property
    def slug(self) -> str:
        return self.dir.name

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.slug

    @property
    def meta_path(self) -> Path:
        return self.dir / "project.json"

    @property
    def source_path(self) -> Path:
        return Path(self.meta["source"])

    @property
    def work_wav(self) -> Path:
        return self.dir / "source.wav"

    @property
    def out_dir(self) -> Path:
        return self.dir / "out"

    def stems_dir(self, mode: str) -> Path:
        return self.dir / "stems" / mode

    def stem(self, mode: str, name: str) -> Path:
        """中間檔路徑（已存在的優先；新產生的用 FLAC）。"""
        d = self.stems_dir(mode)
        for ext in (config.STEM_EXT, ".wav"):
            p = d / f"{name}{ext}"
            if p.exists():
                return p
        if mode == "standard":  # 舊版位置
            legacy = self.dir / "stems" / f"{name}.wav"
            if legacy.exists():
                return legacy
        return d / f"{name}{config.STEM_EXT}"

    # 舊程式碼相容（命令列、測試）
    @property
    def no_vocals(self) -> Path:
        return self.stem(self.default_mode() or "standard", "no_vocals")

    @property
    def vocals(self) -> Path:
        return self.stem(self.default_mode() or "standard", "vocals")

    # ---------- 狀態
    @property
    def has_video(self) -> bool:
        return bool(self.meta.get("has_video"))

    @property
    def modes(self) -> dict:
        return self.meta.setdefault("modes", {})

    def is_analyzed(self, mode: Optional[str] = None) -> bool:
        if mode is None:
            return bool(self.analyzed_modes())
        info = self.modes.get(mode)
        if not info or not info.get("analyzed_at"):
            return False
        if info.get("rev", 1) < config.MIN_SEP_REV.get(mode, 0):
            return False      # 舊版做壞的結果：當作沒分析，會自動重做
        return all(self.stem(mode, n).exists() for n in STEM_NAMES)

    def analyzed_modes(self) -> list[str]:
        return [m for m in config.MODE_ORDER if self.is_analyzed(m)]

    def default_mode(self) -> Optional[str]:
        last = self.meta.get("last_mode")
        if last and self.is_analyzed(last):
            return last
        done = self.analyzed_modes()
        return done[0] if done else None

    def compensation_db(self, mode: str) -> float:
        """去人聲後要補多少 dB 才回到原曲響度（0 ~ MAX）。"""
        src = self.meta.get("loudness")
        nv = self.modes.get(mode, {}).get("nv_lufs")
        if src is None or nv is None:
            return 0.0
        return round(max(0.0, min(config.MAX_COMPENSATION_DB, src - nv)), 2)

    # ---------- 中間檔管理
    def cache_bytes(self) -> int:
        return _dir_size(self.dir / "stems") + _dir_size(self.work_wav)

    def clear_cache(self) -> int:
        """刪除中間檔（分離結果、工作音軌）。輸出的成品與紀錄保留。回傳釋放的位元組數。"""
        freed = self.cache_bytes()
        shutil.rmtree(self.dir / "stems", ignore_errors=True)
        self.work_wav.unlink(missing_ok=True)
        (self.dir / "_render_tmp.wav").unlink(missing_ok=True)
        self.meta["modes"] = {}
        for k in ("analyzed_at", "separation", "last_mode"):
            self.meta.pop(k, None)
        self.save()
        return freed

    # ---------- 讀寫
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.meta_path)

    @classmethod
    def load(cls, song_dir: Path) -> "Song":
        meta = json.loads((song_dir / "project.json").read_text(encoding="utf-8"))
        return cls(dir=song_dir, meta=meta)

    def add_render(self, entry: dict) -> None:
        self.meta.setdefault("renders", []).append({**entry, "at": now_iso()})
        self.save()

    def _migrate(self) -> None:
        """第 1、2 批的資料：analyzed_at + stems/*.wav → modes.standard"""
        if "modes" not in self.meta and self.meta.get("analyzed_at"):
            sep = self.meta.get("separation", {})
            self.meta["modes"] = {"standard": {
                "analyzed_at": self.meta["analyzed_at"],
                "seconds": sep.get("seconds"), "device": sep.get("device"),
                "model": sep.get("model"), "backend": sep.get("backend"),
            }}


def _same_file(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def open_for_source(source: Path | str, songs_dir: Path | None = None) -> Song:
    """找到這個來源檔對應的歌曲資料夾；沒有就建立新的（還沒分析）。"""
    songs_dir = songs_dir or config.SONGS_DIR
    source = Path(source).resolve()
    base = slugify(source.stem)

    n = 1
    while True:
        slug = base if n == 1 else f"{base}-{n}"
        d = songs_dir / slug
        if not (d / "project.json").exists():
            song = Song(dir=d, meta={
                "source": str(source),
                "title": source.stem,
                "created_at": now_iso(),
                "modes": {},
                "renders": [],
            })
            song.save()
            return song
        song = Song.load(d)
        if _same_file(song.meta.get("source", ""), source):
            return song
        n += 1  # 同名但不同檔案 → 用 -2、-3 區分


def find_song(name_or_path: str, songs_dir: Path | None = None) -> Song | None:
    """用檔案路徑或歌名（資料夾名）找歌。"""
    songs_dir = songs_dir or config.SONGS_DIR
    p = Path(name_or_path)
    if p.exists() and p.is_file():
        return open_for_source(p, songs_dir)
    d = songs_dir / name_or_path
    if (d / "project.json").exists():
        return Song.load(d)
    matches = [s for s in list_songs(songs_dir) if name_or_path in s.slug or name_or_path in s.title]
    return matches[0] if len(matches) == 1 else None


def list_songs(songs_dir: Path | None = None) -> list[Song]:
    """最近處理的歌，新的在前。"""
    songs_dir = songs_dir or config.SONGS_DIR
    if not songs_dir.exists():
        return []
    songs = []
    for d in songs_dir.iterdir():
        if (d / "project.json").exists():
            try:
                songs.append(Song.load(d))
            except (OSError, json.JSONDecodeError):
                continue

    def last_touch(s: Song) -> str:
        times = [m.get("analyzed_at") or "" for m in s.modes.values()]
        times += [r.get("at") or "" for r in s.meta.get("renders", [])[-1:]]
        return max(times + [s.meta.get("created_at") or ""])

    return sorted(songs, key=last_touch, reverse=True)
