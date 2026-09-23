"""命令列介面（第 1 批的使用方式；第 2 批的桌面視窗會取代它，但它會一直保留）。

  python -m karaoke info                          檢查環境（顯卡、FFmpeg）
  python -m karaoke make  <檔案> --key -3 --guide 20      分析 + 輸出，一次完成
  python -m karaoke analyze <檔案>                只分析（AI 分離）
  python -m karaoke render <檔案或歌名> --key -3 輸出一個版本（不重跑 AI）
  python -m karaoke list                          最近處理的歌（含中間檔佔用）
  python -m karaoke clean <歌名> | --all          清除中間檔
  python -m karaoke wizard <檔案>                 一問一答（拖檔案到 bat 用的就是這個）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config, ffmpeg, separate
from .export import key_label
from .hardware import detect_device
from .mix import MixError
from .pipeline import PipelineError, analyze, default_mode, mode_status, render
from .project import find_song, list_songs


def _fix_console() -> None:
    # Windows 主控台預設是 cp950，印日文檔名會當掉；統一改成 UTF-8
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_info(_: argparse.Namespace) -> int:
    dev = detect_device()
    print(f"DENKI 伴奏工具 v{__version__}")
    print(f"工作資料夾：{config.ROOT}")
    print(f"運算裝置：{dev['name']}" + ("（GPU 加速）" if dev["device"] == "cuda" else ""))
    try:
        print(f"FFmpeg：{ffmpeg.find('ffmpeg')}")
        print(f"變調濾鏡 rubberband：{'有' if ffmpeg.has_filter('rubberband') else '沒有（無法升降 Key）'}")
    except ffmpeg.FFmpegError as e:
        print(f"FFmpeg：{e}")
    print(f"Demucs：{'已安裝' if separate.demucs_available() else '未安裝'}")
    print(f"高品質分離元件：{'已安裝' if separate.roformer_available() else '未安裝'}")
    print("分離模式：")
    for m, st in mode_status().items():
        spec = config.MODES[m]
        mark = "可用" if st["available"] else f"不可用（{st['reason']}）"
        print(f"  {m:9s} {spec['label']:4s}　{mark}")
    print(f"預設模式：{default_mode()}")
    return 0


def _add_render_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--key", type=int, default=0, help="升降 Key（半音，-12 到 +12，預設 0）")
    p.add_argument("--guide", type=int, default=0, help="導唱音量 0–100%%（保留多少原唱，預設 0）")
    p.add_argument("--format", default="auto", choices=["auto", "video", "mp3", "wav"],
                   help="輸出格式（auto：影片來源出影片，音檔來源出 mp3）")
    p.add_argument("--mp3", action="store_true", help="同時輸出 MP3")
    p.add_argument("--tempo", type=int, default=100, help="速度 %%（練習用，預設 100；影片輸出時忽略）")
    p.add_argument("--out", type=Path, default=None, help="輸出資料夾（預設放在該歌的 out 資料夾）")
    p.add_argument("--no-comp", action="store_true", help="關閉音量補償")
    p.add_argument("--countin", type=int, choices=(0, 1, 2), default=0,
                   help="預備拍小節數（鼓棒互敲，只加在 MP3／WAV；預設 0＝不加）")


def _add_analyze_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backend", default=None, choices=["demucs", "roformer", "fake"], help=argparse.SUPPRESS)
    p.add_argument("--model", default=None, help=argparse.SUPPRESS)
    p.add_argument("--cpu", action="store_true", help="強制用 CPU")
    p.add_argument("--force", action="store_true", help="已分析過也重新分析")


def _add_mode_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", default=None, choices=config.MODE_ORDER,
                   help="分離模式：standard 標準 / hq 高品質 / harmony 保留和聲（預設：有顯卡用 hq）")


def _analyze(a: argparse.Namespace):
    return analyze(a.file, mode=a.mode, backend=a.backend, model=a.model,
                   device="cpu" if a.cpu else None, force=a.force)


def _render(song, a: argparse.Namespace):
    return render(song, mode=a.mode, key=a.key, guide=a.guide, fmt=a.format,
                  also_mp3=a.mp3, tempo=a.tempo, compensate=not a.no_comp, countin=a.countin, out_dir=a.out)


def cmd_analyze(a):
    _analyze(a)
    return 0


def cmd_make(a):
    _render(_analyze(a), a)
    return 0


def cmd_render(a):
    song = find_song(a.song)
    if song is None:
        print(f"找不到「{a.song}」。用 python -m karaoke list 看有哪些歌。")
        return 1
    _render(song, a)
    return 0


def cmd_list(_):
    songs = list_songs()
    if not songs:
        print("還沒有處理過任何歌。")
        return 0
    total = 0
    for s in songs:
        done = [config.MODES[m]["label"] for m in s.analyzed_modes()]
        state = "、".join(done) if done else "未分析"
        kind = "影片" if s.has_video else "音檔"
        n = len(s.meta.get("renders", []))
        size = s.cache_bytes()
        total += size
        print(f"  {s.slug}　（{kind}・{state}・已輸出 {n} 個・中間檔 {size / 1e6:.0f} MB）")
    print(f"\n中間檔合計 {total / 1e6:.0f} MB")
    return 0


def cmd_clean(a):
    songs = list_songs() if a.all else [find_song(a.song)] if a.song else []
    songs = [s for s in songs if s]
    if not songs:
        print("請指定歌名，或用 --all 清除全部。")
        return 1
    freed = sum(s.clear_cache() for s in songs)
    print(f"已清除 {len(songs)} 首歌的中間檔，釋放 {freed / 1e6:.0f} MB。輸出的成品都還在。")
    return 0


def _ask_int(prompt: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = input(f"{prompt}（{lo}～{hi}，直接 Enter = {default}）：").strip()
        if not raw:
            return default
        try:
            v = int(raw.replace("+", ""))
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
        print("  請輸入範圍內的整數。")


def cmd_wizard(a):
    """給「拖檔案到 bat」用：一問一答，不用記參數。"""
    path = a.file
    if path is None:
        cmd_list(a)
        print()
        raw = input("把歌曲檔（音檔或影片）拖進這個視窗，再按 Enter：").strip().strip('"').strip("'").strip()
        if not raw:
            return 0
        path = Path(raw)
    song = analyze(path)
    mode = song.default_mode()
    print()
    print("—— 分析完成，可以開始調整 ——")
    while True:
        key = _ask_int("升降幾個 Key？女轉男常用 -5，男轉女常用 +5", 0, config.KEY_MIN, config.KEY_MAX)
        guide = _ask_int("保留多少原唱當導唱（%）", 0, 0, 100)
        fmt = "video" if song.has_video else "mp3"
        also_mp3 = False
        if song.has_video:
            also_mp3 = input("影片之外，要不要同時輸出 MP3？(y/N)：").strip().lower() == "y"
        render(song, mode=mode, key=key, guide=guide, fmt=fmt, also_mp3=also_mp3)
        again = input(f"\n再出一個版本嗎？（剛才是 {key_label(key)}）(y/N)：").strip().lower()
        if again != "y":
            return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m karaoke", description="DENKI 伴奏工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="檢查環境").set_defaults(func=cmd_info)
    sub.add_parser("list", help="最近處理的歌").set_defaults(func=cmd_list)

    s = sub.add_parser("analyze", help="只分析（AI 分離）")
    s.add_argument("file", type=Path)
    _add_mode_arg(s)
    _add_analyze_args(s)
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("make", help="分析 + 輸出一次完成")
    s.add_argument("file", type=Path)
    _add_mode_arg(s)
    _add_analyze_args(s)
    _add_render_args(s)
    s.set_defaults(func=cmd_make)

    s = sub.add_parser("render", help="輸出一個版本（不重跑 AI）")
    s.add_argument("song", help="原檔路徑或歌名")
    _add_mode_arg(s)
    _add_render_args(s)
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("clean", help="清除中間檔（輸出的成品不會刪）")
    s.add_argument("song", nargs="?", default=None, help="歌名")
    s.add_argument("--all", action="store_true", help="全部清除")
    s.set_defaults(func=cmd_clean)

    s = sub.add_parser("wizard", help="一問一答模式")
    s.add_argument("file", type=Path, nargs="?", default=None)
    s.set_defaults(func=cmd_wizard)
    return p


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (PipelineError, MixError, ffmpeg.FFmpegError, separate.SeparationError) as e:
        print(f"\n錯誤：{e}")
        return 1
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
