"""高品質模式（BS-RoFormer）在顯卡上分不出人聲的診斷。

用一首已分析過的歌剪 8 秒，在顯卡上用幾種不同設定各跑一次，量人聲軌的音量：
人聲軌有聲音 = 這個設定正常；-90 dB 左右 = 全是零（壞掉）。
結果寫到 <安裝資料夾>\\高品質診斷.txt

  python -m tests.hq_diag
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from karaoke import config, ffmpeg  # noqa: E402

MODEL = config.MODES["hq"]["model"]

# 每個設定＝在 audio-separator 開始前先執行的一段修補
VARIANTS = {
    "A 原本的設定": "",
    "B 注意力只用 math": (
        "from torch.nn.attention import SDPBackend\n"
        "import audio_separator.separator.uvr_lib_v5.roformer.attend as A\n"
        "A._sdpa_backends = lambda config: [SDPBackend.MATH]\n"
    ),
    "C 拿掉 cuDNN 注意力": (
        "import audio_separator.separator.uvr_lib_v5.roformer.attend as A\n"
        "_orig = A._sdpa_backends\n"
        "from torch.nn.attention import SDPBackend\n"
        "A._sdpa_backends = lambda config: [b for b in _orig(config) if b != SDPBackend.CUDNN_ATTENTION]\n"
    ),
    "D 關閉 flash 注意力": (
        "import audio_separator.separator.uvr_lib_v5.roformer.attend as A\n"
        "_init = A.Attend.__init__\n"
        "def _no_flash(self, *a, **k):\n"
        "    k['flash'] = False\n"
        "    _init(self, *a, **k)\n"
        "A.Attend.__init__ = _no_flash\n"
    ),
    "E 關閉 TF32": (
        "import torch\n"
        "torch.backends.cuda.matmul.allow_tf32 = False\n"
        "torch.backends.cudnn.allow_tf32 = False\n"
        "torch.set_float32_matmul_precision('highest')\n"
    ),
}

RUNNER = (
    "import sys\n"
    "{patch}"
    "from audio_separator.utils.cli import main\n"
    "sys.argv = ['audio-separator', {src!r}, '--model_filename', {model!r}, '--model_file_dir', {mdir!r},"
    " '--output_dir', {out!r}, '--output_format', 'WAV']\n"
    "main()\n"
)


def level_db(path: Path) -> float:
    import numpy as np
    x = ffmpeg.decode_pcm(path, 22050)
    rms = float(np.sqrt(np.mean(x.astype("float64") ** 2))) if len(x) else 0.0
    return round(20 * np.log10(rms + 1e-12), 1)


def main() -> int:
    lines: list[str] = []

    def say(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    env_info = subprocess.run(
        [sys.executable, "-c",
         "import torch, json; ok=torch.cuda.is_available(); print(json.dumps({'torch': torch.__version__,"
         " 'cuda': torch.version.cuda, 'cudnn': torch.backends.cudnn.version(), 'gpu': torch.cuda.get_device_name(0) if ok else None,"
         " 'cap': torch.cuda.get_device_capability(0) if ok else None}))"],
        capture_output=True, text=True).stdout.strip()
    say(f"DENKI 高品質模式診斷　{time.strftime('%Y-%m-%d %H:%M')}")
    say(f"環境：{env_info}")

    songs = sorted(config.SONGS_DIR.glob("*/source.wav"), key=lambda p: p.stat().st_mtime)
    if not songs:
        say("找不到分析過的歌（songs\\*\\source.wav），請先用程式分析任何一首歌。")
        return 1
    src = songs[-1]
    say(f"使用：{src.parent.name}（第 60 秒起 8 秒）")

    with tempfile.TemporaryDirectory(prefix="denki-hqdiag-") as tmp:
        tmp = Path(tmp)
        clip = tmp / "clip.wav"
        ffmpeg.run(["-ss", "60", "-t", "8", "-i", src, clip])
        say(f"原曲片段音量：{level_db(clip)} dB")
        say("")
        for name, patch in VARIANTS.items():
            out = tmp / name[0]
            out.mkdir()
            code = RUNNER.format(patch=patch, src=str(clip), model=MODEL, mdir=str(config.MODELS_DIR), out=str(out))
            t0 = time.perf_counter()
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            secs = time.perf_counter() - t0
            voc = list(out.glob("*(Vocals)*"))
            ins = list(out.glob("*(Instrumental)*"))
            if proc.returncode != 0 or not voc:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
                say(f"{name}：執行失敗（{secs:.0f} 秒）{' / '.join(tail)[-300:]}")
                continue
            v, i = level_db(voc[0]), level_db(ins[0]) if ins else None
            verdict = "✅ 正常" if v > -50 else "❌ 人聲是零"
            say(f"{name}：人聲 {v} dB、伴奏 {i} dB　{verdict}（{secs:.0f} 秒）")

    say("")
    say("把這份結果貼給 Claude。")
    (config.ROOT / "高品質診斷.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
