"""高品質模式診斷（第 2 輪）：找出顯卡上從哪一層開始算錯。

1. 同一段 8 秒音訊，模型在「顯卡」和「CPU」上各跑一次，逐層比較輸出（哪一層開始差很多＝兇手）
2. 用 CPU 完整跑一次分離，確認你電腦上的 CPU 版本是正常的
結果寫到 <安裝資料夾>\\高品質診斷2.txt

  python -m tests.hq_diag2
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from karaoke import config, ffmpeg  # noqa: E402

MODEL = config.MODES["hq"]["model"]
LINES: list[str] = []


def say(s: str = "") -> None:
    print(s, flush=True)
    LINES.append(s)


def level_db(path: Path) -> float:
    import numpy as np
    x = ffmpeg.decode_pcm(path, 22050)
    rms = float(np.sqrt(np.mean(x.astype("float64") ** 2))) if len(x) else 0.0
    return round(20 * np.log10(rms + 1e-12), 1)


def layer_probe(clip: Path, tmp: Path) -> None:
    import numpy as np
    import torch
    from audio_separator.separator import Separator

    sep = Separator(model_file_dir=str(config.MODELS_DIR), output_dir=str(tmp), log_level=40)
    sep.load_model(model_filename=MODEL)
    model = sep.model_instance.model_run
    model.eval()
    p = next(model.parameters())
    say(f"模型參數：{p.dtype}，放在 {p.device}")

    x = ffmpeg.decode_pcm(clip, 44100)  # mono
    audio = torch.tensor(np.stack([x, x])[:, :352800], dtype=torch.float32).unsqueeze(0)

    watch = {"band_split": model.band_split}
    for i, blk in enumerate(model.layers):
        watch[f"layers[{i}]"] = blk[-1]          # 每一層最後的 freq transformer
    watch["final_norm"] = model.final_norm
    watch["mask_estimator"] = model.mask_estimators[0]

    def run(device: str) -> tuple[dict, "torch.Tensor"]:
        stats: dict[str, tuple[float, int]] = {}
        hooks = []
        for name, mod in watch.items():
            def hook(_m, _i, out, name=name):
                t = out[0] if isinstance(out, (tuple, list)) else out
                t = t.detach().float()
                stats[name] = (float(t.abs().mean()), int((~torch.isfinite(t)).sum()), t.cpu())
            hooks.append(mod.register_forward_hook(hook))
        model.to(device)
        with torch.no_grad():
            out = model(audio.to(device))
        for h in hooks:
            h.remove()
        return stats, out.detach().float().cpu()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.perf_counter()
    g_stats, g_out = run(dev)
    say(f"{dev} 跑一段：{time.perf_counter() - t0:.1f} 秒")
    t0 = time.perf_counter()
    c_stats, c_out = run("cpu")
    say(f"cpu 跑一段：{time.perf_counter() - t0:.1f} 秒")
    say("")
    say(f"{'位置':<16}{dev + ' 平均':>12}{'cpu 平均':>12}{'相對誤差':>10}{'壞值':>8}")
    for name in watch:
        g, c = g_stats.get(name), c_stats.get(name)
        if not g or not c:
            say(f"{name:<16}（沒有被呼叫）")
            continue
        err = float((g[2] - c[2]).abs().mean() / (c[2].abs().mean() + 1e-12))
        flag = "  ← 開始不一樣" if err > 0.05 else ""
        say(f"{name:<16}{g[0]:>12.4g}{c[0]:>12.4g}{err:>10.3f}{g[1]:>8d}{flag}")
    say("")
    say(f"最後輸出（人聲）平均：{dev} {float(g_out.abs().mean()):.4g}、cpu {float(c_out.abs().mean()):.4g}")


def cpu_full_run(clip: Path, tmp: Path) -> None:
    out = tmp / "cpu"
    out.mkdir()
    code = (
        "import sys\nfrom audio_separator.utils.cli import main\n"
        f"sys.argv = ['audio-separator', {str(clip)!r}, '--model_filename', {MODEL!r}, '--model_file_dir', "
        f"{str(config.MODELS_DIR)!r}, '--output_dir', {str(out)!r}, '--output_format', 'WAV']\nmain()\n"
    )
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "-1"}
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    voc = list(out.glob("*(Vocals)*"))
    if proc.returncode != 0 or not voc:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        say(f"CPU 完整分離：失敗 {' / '.join(tail)[-300:]}")
        return
    v = level_db(voc[0])
    say(f"CPU 完整分離：人聲 {v} dB　{'✅ 正常' if v > -50 else '❌ 人聲是零'}（{time.perf_counter() - t0:.0f} 秒）")


def main() -> int:
    import torch
    say(f"DENKI 高品質模式診斷（第 2 輪）　{time.strftime('%Y-%m-%d %H:%M')}")
    say(f"torch {torch.__version__}，CUDA {torch.version.cuda}，"
        f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else '沒有顯卡'}")
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
        say(f"顯卡驅動：{smi or '?'}")
    except Exception:  # noqa: BLE001
        say("顯卡驅動：?")
    songs = sorted(config.SONGS_DIR.glob("*/source.wav"), key=lambda p: p.stat().st_mtime)
    if not songs:
        say("找不到分析過的歌。")
        return 1
    src = songs[-1]
    say(f"使用：{src.parent.name}（第 60 秒起 8 秒）")
    with tempfile.TemporaryDirectory(prefix="denki-hqdiag2-") as tmp:
        tmp = Path(tmp)
        clip = tmp / "clip.wav"
        ffmpeg.run(["-ss", "60", "-t", "8", "-i", src, clip])
        say("")
        say("【1】逐層比較（顯卡 vs CPU，同一段音訊）")
        try:
            layer_probe(clip, tmp)
        except Exception as e:  # noqa: BLE001
            import traceback
            say(f"逐層比較失敗：{e}")
            say(traceback.format_exc()[-800:])
        say("")
        say("【2】CPU 完整分離（比較慢，約 1～5 分鐘）")
        cpu_full_run(clip, tmp)
    say("")
    say("把這份結果貼給 Claude。")
    (config.ROOT / "高品質診斷2.txt").write_text("\n".join(LINES), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
