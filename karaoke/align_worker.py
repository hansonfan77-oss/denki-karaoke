"""AI 對時間的子程序：python -m karaoke.align_worker <job.json>

job.json：{audio, text, lang, model, model_dir, out}
stdout 每行一個 JSON：{"stage":"load","device":"cuda"}、{"progress":[已處理秒數, 總秒數]}
結果寫到 out：{"words":[{"text","start","end"}, …]} 或 {"error": "…"}
"""

from __future__ import annotations

import json
import sys


def trim_to_voice(words: list[dict], audio, sr: int = 16000) -> list[dict]:
    """AI 常把一句最後一個字拖進間奏／尾奏（人聲軌已經安靜了還算在唱）。
    用人聲軌的音量檢查：字開始之後，只要人聲連續安靜 0.35 秒，這個字就在安靜開始的地方結束。"""
    import numpy as np

    hop = int(sr * 0.02)                       # 20 毫秒一格
    n = len(audio) // hop
    if n < 10:
        return words
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1))
    loud = float(np.percentile(rms, 95))
    if loud <= 1e-6:
        return words
    silent = rms < max(loud * 0.06, 1e-4)
    need = int(0.35 / 0.02)
    # quiet_run[i]：從第 i 格開始連續安靜幾格
    quiet_run = np.zeros(n + 1, dtype=np.int32)
    for i in range(n - 1, -1, -1):
        quiet_run[i] = quiet_run[i + 1] + 1 if silent[i] else 0
    out = []
    for w in words:
        s, e = w["start"], w["end"]
        a = int((s + 0.15) / 0.02)
        b = int(e / 0.02)
        new_e = e
        for i in range(max(0, a), min(n, b)):
            if quiet_run[i] >= need:
                new_e = max(s + 0.1, i * 0.02)
                break
        out.append({**w, "end": round(min(e, new_e), 3)})
    return out


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    job = json.loads(open(sys.argv[1], encoding="utf-8").read())
    out_path = job["out"]

    def fail(msg: str) -> int:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"error": msg}, f, ensure_ascii=False)
        return 0

    try:
        import torch
        import stable_whisper
    except Exception as e:  # noqa: BLE001
        return fail(f"AI 對時間元件載入失敗：{e}")

    from karaoke import ffmpeg

    device = "cuda" if torch.cuda.is_available() else "cpu"
    emit({"stage": "load", "device": "顯卡" if device == "cuda" else "CPU"})
    # 用名稱（不是檔案路徑）載入：Whisper 才會套用這個模型專用的「對齊注意力頭」，時間比較準。
    # download_root 裡已經有核對過的檔案，所以不會再下載。
    model = stable_whisper.load_model(job["model"], device=device, download_root=job["model_dir"])
    audio = ffmpeg.decode_pcm(job["audio"], 16000).copy()   # 可寫入的陣列（PyTorch 需要）
    if len(audio) < 16000:
        return fail("人聲音檔太短或是空的。")

    def on_progress(done: float, total: float) -> None:
        emit({"progress": [round(float(done), 2), round(float(total), 2)]})

    result = model.align(
        audio, job["text"], language=job["lang"],
        original_split=True,          # 一行一句，保持使用者貼上的斷句
        verbose=None,                 # 不印進度條（進度改用 progress_callback 回報）
        progress_callback=on_progress,
        nonspeech_skip=3.0,           # 間奏（人聲軌是安靜的）直接跳過，避免歌詞被拉進間奏
        ignore_compatibility=True,    # 版本已鎖定驗證過，不用再警告
    )
    if result is None:
        return fail("AI 對不上這份歌詞，請確認歌詞和歌曲是同一首。")
    words = [{"text": w.word, "start": float(w.start), "end": float(w.end)} for w in result.all_words()]
    words = trim_to_voice(words, audio)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"words": words, "device": device}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
