"""自動測試 E 段：卡拉影片的歌詞與 AI 對時間（第 4 批）。由 tests/selftest.py 呼叫。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from karaoke import align, config, ffmpeg, kvideo, lyrics, pipeline
from karaoke.project import Song

ROOT_DIR = Path(__file__).resolve().parents[1]

LRC_SAMPLE = """[ti:測試]
[ar:DENKI]
[offset:+200]
[00:05.00]第一句 <00:05.50>逐字
[00:10.50][01:00.00]副歌
[00:14.00]
[00:20.25]最後一句
"""


def _serve_bytes(routes: dict):
    """本機假伺服器：{路徑: (狀態碼, bytes)}。回傳 (網址, server)。"""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            code, body = routes.get(self.path.split("?")[0], (404, b"{}"))
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def lyrics_tests(r, make_test_video) -> None:
    r.info("")
    r.info("【E. 卡拉影片：歌詞與對時間】")

    # 1. LRC
    ls = lyrics.parse_lrc(LRC_SAMPLE)
    texts = [x["text"] for x in ls]
    r.check("LRC 解析", texts == ["第一句 逐字", "副歌", "最後一句", "副歌"]
            and abs(ls[0]["t"] - 4.8) < 1e-6 and ls[1]["end"] is not None and abs(ls[1]["end"] - 13.8) < 1e-6,
            f"{len(ls)} 句（重複標籤、逐字標籤、offset、空白結束標籤）")
    back = lyrics.parse_lrc(lyrics.format_lrc(ls, offset=1.0))
    r.check("LRC 輸出再讀回", [x["text"] for x in back] == texts and abs(back[0]["t"] - 5.8) < 0.011
            and back[1]["end"] is not None and abs(back[1]["end"] - 14.8) < 0.011, "時間、結束標籤都一致")
    plain = lyrics.split_plain("[Chorus]\n街の灯り\n\n  君の声  \n[00:01.00]時間標籤會去掉\n")
    r.check("貼上純文字整理", plain == ["街の灯り", "君の声", "時間標籤會去掉"], " / ".join(plain))

    # 2. 歌名、語言
    cases = {
        "【リゼロ】劇中歌『Stay Alive ～Regain～』｜高橋李依": "Stay Alive ～Regain～",
        "4、桃源恋歌": "桃源恋歌",
        "10 天使にふれたよ!": "天使にふれたよ!",
        "【ニコカラ】UNDEAD _ YOASOBI【 On vocal": "UNDEAD YOASOBI",
    }
    wrong = {k: lyrics.guess_title(k) for k, v in cases.items() if lyrics.guess_title(k) != v}
    r.check("從檔名猜歌名", not wrong, f"{len(cases)} 種檔名" if not wrong else f"{wrong}")
    langs = [lyrics.detect_language(s) for s in ("君の声だけ", "我們一起唱歌吧", "사랑해요 너를", "hello world")]
    r.check("判斷歌詞語言", langs == ["ja", "zh", "ko", "en"], "、".join(langs))

    # 3. AI 的逐字時間 → 每一句
    words = [{"text": " 街の", "start": 1.0, "end": 1.5}, {"text": "灯り", "start": 1.5, "end": 2.2},
             {"text": "、君", "start": 5.0, "end": 5.4}, {"text": "の声", "start": 5.4, "end": 6.0}]
    m = lyrics.map_words_to_lines(words, ["街の灯り", "君の声"])
    got = [(x["t"], x["end"]) for x in m]
    fixed = lyrics.repair_words([{"text": "あ", "t": 1.0, "end": 1.4}, {"text": "い", "t": 1.4, "end": 1.4},
                                 {"text": "う", "t": 1.4, "end": 1.8}, {"text": "え！", "t": 240.0, "end": 240.0}], 1.0, 9.0)
    r.check("修正 AI 找不到的字", bool(fixed) and all(w["end"] - w["t"] >= 0.05 for w in fixed) and fixed[-1]["end"] < 3,
            "長度 0 的字分到空檔、被丟到整首最後的字接回句尾")
    r.check("逐字時間對應回每一句", got == [(1.0, 2.2), (5.0, 6.0)], f"{got}")

    # 4. LRCLIB（本機假伺服器）
    fake = [
        {"id": 1, "trackName": "桃源恋歌", "artistName": "A", "duration": 250, "plainLyrics": "a\nb",
         "syncedLyrics": None, "instrumental": False},
        {"id": 2, "trackName": "桃源恋歌", "artistName": "B", "duration": 236, "plainLyrics": "x",
         "syncedLyrics": "[00:01.00]x\n[00:03.00]y", "instrumental": False},
        {"id": 3, "trackName": "桃源恋歌 (Inst.)", "artistName": "C", "duration": 235, "plainLyrics": None,
         "syncedLyrics": None, "instrumental": True},
    ]
    url, srv = _serve_bytes({"/api/search": (200, json.dumps(fake).encode())})
    saved_api = lyrics.LRCLIB_API
    try:
        lyrics.LRCLIB_API = url + "/api"
        res = lyrics.search_lrclib("桃源恋歌", duration=235)
        r.check("LRCLIB 搜尋與排序", [x["id"] for x in res] == [2, 1] and res[0]["synced"]
                and res[0]["durationDiff"] == 1.0, "附時間的在前、純音樂版不列出")
        lyrics.LRCLIB_API = "http://127.0.0.1:9/api"
        try:
            lyrics.search_lrclib("桃源恋歌")
            r.fail("連不上 LRCLIB 的提示", "沒有丟出錯誤")
        except lyrics.LyricsError as e:
            r.ok("連不上 LRCLIB 的提示", str(e))
    finally:
        lyrics.LRCLIB_API = saved_api
        srv.shutdown()

    # 5. 歌詞存檔、AI 對時間（測試模式）、模型下載
    with tempfile.TemporaryDirectory(prefix="denki-lyrics-") as tmp:
        tmp = Path(tmp)
        src = tmp / "桃源恋歌 測試.mp4"
        make_test_video(src)
        song = pipeline.analyze(src, backend="fake", songs_dir=tmp / "songs", log=lambda *_: None)
        lyrics.save(song, ls, source="lrc", offset=1.25)
        again = lyrics.load(Song.load(song.dir))
        r.check("歌詞存檔與讀回", bool(again) and len(again["lines"]) == 4 and again["offset"] == 1.25
                and again["lang"] == "zh", f"4 句，offset {again['offset'] if again else '-'}")
        song.clear_cache()
        r.check("清除中間檔不會刪歌詞", lyrics.load(Song.load(song.dir)) is not None, "lyrics.json 還在")
        try:
            align.vocals_for(Song.load(song.dir))
            r.fail("沒有人聲中間檔會提示", "沒有擋下")
        except align.AlignError as e:
            r.ok("沒有人聲中間檔會提示", str(e)[:40] + "…")
        song = pipeline.analyze(src, backend="fake", songs_dir=tmp / "songs", log=lambda *_: None)

        os.environ["DENKI_ALIGN_FAKE"] = "1"
        try:
            got = align.align_song(song, "街の灯りが\n君の声だけ\n\n走り出した")
            ts = [x["t"] for x in got["lines"]]
            r.check("AI 對時間流程（測試模式）", got["source"] == "ai" and got["lang"] == "ja" and len(ts) == 3
                    and ts == sorted(ts) and all(x["end"] and x["end"] > x["t"] for x in got["lines"]),
                    f"3 句：{', '.join(f'{t:.1f}' for t in ts)} 秒")
            r.check("AI 對時間會存逐字時間", all(x.get("words") and "".join(w["text"] for w in x["words"]) == x["text"]
                                            for x in got["lines"]), "每句都有，段落接起來＝原句")
            # 精修掃色：使用者調過的開始時間不能被改掉
            mine = [dict(x, t=x["t"] + 1.5, words=None) for x in got["lines"]]
            lyrics.save(song, mine, source="lrclib", offset=0.8)
            ref = align.refine_song(song)
            r.check("AI 精修掃色保留原本的開始時間", [x["t"] for x in ref["lines"]] == [x["t"] for x in mine]
                    and all(x.get("words") and abs(x["words"][0]["t"] - x["t"]) < 0.01 for x in ref["lines"])
                    and ref["source"] == "lrclib" and ref["offset"] == 0.8, "開始時間、來源、整體位移都沒變，補上逐字時間")

            # v0.6.3：逐句切換「均分」（逐字時間保留）＋單句精修
            sw = [dict(x) for x in ref["lines"]]
            sw[0]["even"] = True
            sw[1] = {k: v for k, v in sw[1].items() if k != "words"}
            lyrics.save(song, sw, source="lrclib", offset=0.8)
            back = lyrics.load(Song.load(song.dir))["lines"]
            from karaoke import kvideo
            tl = kvideo.timed(back, 0.8)
            r.check("逐句切成均分：逐字時間保留、輸出用均分", back[0].get("even") is True and bool(back[0].get("words"))
                    and tl[0]["words"] is None and tl[2]["words"] is not None, "第 1 句均分、第 3 句逐字")
            one = align.refine_song(song, line=1)
            ol = one["lines"]
            r.check("單句精修只改那一句", bool(ol[1].get("words")) and not ol[1].get("even")
                    and abs(ol[1]["words"][0]["t"] - ol[1]["t"]) < 0.01
                    and [x["t"] for x in ol] == [x["t"] for x in sw]
                    and ol[0].get("even") is True and ol[0]["words"] == back[0]["words"],
                    "第 2 句補上逐字，其他句（含均分設定）不動")

            payload = os.urandom(3_000_000)
            url, srv = _serve_bytes({"/m.pt": (200, payload)})
            os.environ["DENKI_ALIGN_MODEL_URL"] = url + "/m.pt"
            os.environ["DENKI_ALIGN_MODEL_SHA256"] = hashlib.sha256(payload).hexdigest()
            saved_models = config.MODELS_DIR
            config.MODELS_DIR = tmp / "models"
            part = lambda: align.model_file().with_suffix(".pt.part")  # noqa: E731
            try:
                align.download_model(lambda *a: None)
                r.check("下載對時間模型（核對 SHA-256）",
                        align.model_ready() and align.model_file().stat().st_size == len(payload), "3 MB 假模型")
                os.environ["DENKI_ALIGN_MODEL_SHA256"] = "0" * 64
                try:
                    align.download_model(lambda *a: None)
                    r.fail("模型核對不符會擋下", "沒有丟出錯誤")
                except align.AlignError as e:
                    r.check("模型核對不符會擋下", not align.model_ready() and not part().exists(), str(e))
                tok = align.CancelToken()
                tok.cancel()
                try:
                    align.download_model(lambda *a: None, tok)
                    r.fail("下載可以取消", "沒有停下來")
                except align.Cancelled:
                    r.check("下載可以取消", not part().exists(), "半截檔案已刪除")
            finally:
                config.MODELS_DIR = saved_models
                os.environ.pop("DENKI_ALIGN_MODEL_URL", None)
                os.environ.pop("DENKI_ALIGN_MODEL_SHA256", None)
                srv.shutdown()
        finally:
            os.environ.pop("DENKI_ALIGN_FAKE", None)

    # 6. 真的元件載得進來（安裝了的電腦才檢查）
    if importlib.util.find_spec("stable_whisper"):
        t0 = time.perf_counter()
        out = subprocess.run([sys.executable, "-c", "import stable_whisper, whisper; print('ok', whisper.__version__)"],
                             cwd=ROOT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = out.stdout.strip().startswith("ok")
        r.check("AI 對時間元件可以載入", ok,
                f"whisper {out.stdout.strip()[3:]}，{time.perf_counter() - t0:.1f} 秒" if ok
                else (out.stderr or "").strip()[-300:])
        r.info(f"   對時間模型：{'已下載' if align.model_ready() else '還沒下載（第一次用到才下載，約 1.5 GB）'}")
    else:
        r.info("   （這台沒有安裝 AI 對時間元件，略過載入檢查）")


def karaoke_video_tests(r, make_test_video) -> None:
    """第 5 批：字幕（ASS）規則與輸出 MP4。"""
    r.info("")
    r.info("【F. 卡拉影片：字幕樣式與輸出】")
    lines = [{"t": t, "end": None, "text": f"第{i + 1}句歌詞測試"} for i, t in enumerate([6, 10, 14, 18, 30, 34])]
    ass = kvideo.build_ass(lines, 0.5, kvideo.DEFAULT_STYLE, 45)
    lyr = [x for x in ass.splitlines() if x.startswith("Dialogue: 2,")]
    dots = [x for x in ass.splitlines() if x.startswith("Dialogue: 1,")]
    plates = [x for x in ass.splitlines() if x.startswith("Dialogue: 0,")]
    r.check("字幕：每句一行、上下交替", len(lyr) == 6 and "\\an7" in lyr[0] and "\\an9" in lyr[1] and "\\kf" in lyr[0],
            f"{len(lyr)} 句")
    r.check("字幕：前奏與間奏倒數", len(dots) == 6 and "0:00:03.50" in dots[0], f"{len(dots)} 個倒數畫面（開頭、間奏各 3）")
    r.check("字幕：底板只在有字幕時出現", len(plates) == 2, f"{len(plates)} 段")
    wide = kvideo.fit_size("あ" * 40, 64, 70)
    r.check("太長的句子自動縮小", wide < 64 and kvideo.text_width("あ" * 40, wide) <= 1920 - 140 + 1, f"字高 64 → {wide:.0f}")
    no_plate = kvideo.build_ass(lines, 0, {**kvideo.DEFAULT_STYLE, "plate": "none", "countdown": False, "sweep": False}, 45)
    wl = [{"t": 2.0, "end": 4.0, "text": "あいう、えお", "words": [
        {"text": "あい", "t": 2.0, "end": 2.4}, {"text": "う、", "t": 2.4, "end": 3.2}, {"text": "えお", "t": 3.6, "end": 4.0}]}]
    wass = [x for x in kvideo.build_ass(wl, 0, kvideo.DEFAULT_STYLE, 10).splitlines() if x.startswith("Dialogue: 2,")][0]
    line_only = [x for x in kvideo.build_ass([{**wl[0], "even": True}], 0, kvideo.DEFAULT_STYLE, 10).splitlines()
                 if x.startswith("Dialogue: 2,")][0]
    r.check("逐字掃色字幕", wass.count("\\kf") == 3 and "\\k40}" in wass and line_only.count("\\kf") == 1,
            "每段各自掃色、段落間的空檔保留；這句切成均分＝整句一段")
    r.check("樣式開關（不用底板、不倒數、不掃色）", "Dialogue: 0," not in no_plate and "Dialogue: 1," not in no_plate
            and "\\kf" not in no_plate, "都有作用")

    # v0.7.0：漢字標假名
    from karaoke import furigana
    if furigana.available():
        got = furigana.auto_line("夜の街に灯りがともる")
        pairs = [("夜の街に灯りがともる"[x["s"]:x["e"]], x["r"]) for x in got]
        r.check("自動標假名（送り仮名分開）", pairs == [("夜", "よる"), ("街", "まち"), ("灯", "あか")], str(pairs))
        mine = [{"t": 1.0, "end": 3.0, "text": "本気で走る", "ruby": [{"s": 0, "e": 2, "r": "まじ", "m": True}]},
                {"t": 4.0, "end": 6.0, "text": "君の声"}]
        filled, n = furigana.fill(mine, redo=True)
        r.check("重新標音保留手改的讀音", filled[0]["ruby"][0] == {"s": 0, "e": 2, "r": "まじ", "m": True}
                and [x["r"] for x in filled[1]["ruby"]] == ["きみ", "こえ"], f"改了 {n} 句")
    else:
        r.info("   （這台沒有安裝日文讀音元件，略過自動標音檢查）")
    bad = furigana.clean_ruby([{"s": 0, "e": 1, "r": "よ"}, {"s": 0, "e": 2, "r": "x"}, {"s": 5, "e": 99, "r": "y"},
                               {"s": 2, "e": 3, "r": "", "m": True}, {"s": 3, "e": 4, "r": ""}], "夜の街に")
    r.check("讀音存檔檢查（重疊、超出範圍丟掉）", bad == [{"s": 0, "e": 1, "r": "よ"}, {"s": 2, "e": 3, "r": "", "m": True}],
            str(bad))
    rl = [{"t": 2.0, "end": 4.0, "text": "夜の街", "ruby": [{"s": 0, "e": 1, "r": "よる"}, {"s": 2, "e": 3, "r": "まち"}]}]
    fst = {**kvideo.DEFAULT_STYLE, "furigana": True}
    rass = kvideo.build_ass(rl, 0, fst, 10).splitlines()
    rub = [x for x in rass if x.startswith("Dialogue: 3,")]
    g0, g1 = kvideo.geometry(kvideo.DEFAULT_STYLE), kvideo.geometry(fst)
    r.check("假名字幕：每段一個、跟著掃色、版面留位置", len(rub) == 2 and "よる" in rub[0] and "\\kf" in rub[1]
            and g1["plateH"] > g0["plateH"] and not [x for x in kvideo.build_ass(rl, 0, kvideo.DEFAULT_STYLE, 10).splitlines()
                                                     if x.startswith("Dialogue: 3,")],
            f"{len(rub)} 段假名，底板 {g0['plateH']:.0f} → {g1['plateH']:.0f}")
    r.check("字幕大小上限 140", kvideo.normalize_style({"size": 200})["size"] == 140, "")

    with tempfile.TemporaryDirectory(prefix="denki-kv-test-") as tmp:
        tmp = Path(tmp)
        src = tmp / "卡拉 測試!.mp4"
        make_test_video(src)
        song = pipeline.analyze(src, backend="fake", songs_dir=tmp / "songs", log=lambda *_: None)
        lyrics.save(song, [{"t": 1.0, "end": 2.5, "text": "街の灯りが"}, {"t": 3.0, "end": 5.0, "text": "君の声だけ"}],
                    source="manual", offset=0)
        saved_root = config.ROOT
        config.ROOT = tmp          # 設定檔寫到暫存資料夾，不要動到真的 settings.json
        try:
            kvideo.save_settings(song, {"audio": {"key": -2, "guide": 20}})
            t0 = time.perf_counter()
            res = kvideo.render(song, out_dir=tmp / "out")
            secs = time.perf_counter() - t0
            mp4 = Path(res["files"][0])
            info = ffmpeg.probe(mp4)
            import json as _j
            from karaoke.proc import NO_WINDOW
            pr = subprocess.run([ffmpeg.find("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height", "-of", "json", str(mp4)], capture_output=True, text=True, **NO_WINDOW)
            st = (_j.loads(pr.stdout or "{}").get("streams") or [{}])[0]
            r.check("輸出卡拉影片（原影片背景）", st.get("width") == 1920 and st.get("height") == 1080
                    and abs(info["duration"] - 6) < 0.3 and mp4.name == "卡拉 測試!_卡拉_-2key_導唱20.mp4",
                    f"{mp4.name}，{st.get('width')}×{st.get('height')}，{info['duration']:.1f} 秒，{res['encoder']}，{secs:.1f} 秒")
            lrc = [Path(f) for f in res["files"] if f.endswith(".lrc")]
            r.check("同時輸出 .lrc", bool(lrc) and len(lyrics.parse_lrc(lrc[0].read_text(encoding="utf-8-sig"))) == 2,
                    lrc[0].name if lrc else "沒有產生")
            kvideo.save_settings(song, {"background": {"kind": "color", "color": "#10302A"}})
            res2 = kvideo.render(song, out_dir=tmp / "out2", also_lrc=False)
            r.check("單色背景輸出", res2["background"] == "color" and Path(res2["files"][0]).exists(), res2["encoder"])
            img = tmp / "直式背景.png"
            ffmpeg.run(["-f", "lavfi", "-i", "testsrc=size=600x900", "-frames:v", "1", img])
            kvideo.save_bg_image(song, img.name, img.read_bytes())
            for fit in ("fit", "center"):
                kvideo.save_settings(song, {"background": {"kind": "image", "fit": fit}})
                res3 = kvideo.render(song, out_dir=tmp / f"out_{fit}", also_lrc=False)
                r.check(f"自選圖片背景（{'完整顯示' if fit == 'fit' else '置中'}）",
                        res3["background"] == "image" and Path(res3["files"][0]).exists(), "直式圖片、模糊補邊")
        finally:
            config.ROOT = saved_root

    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if win_fonts.is_dir():
        have = [v["label"] for v in kvideo.FONTS.values() if any((win_fonts / f).exists() for f in v["files"])]
        r.check("字幕字型", "微軟正黑體" in have, "、".join(have) or "一個都沒有")
