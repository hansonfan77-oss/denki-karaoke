"""全域設定：路徑、支援的格式、分離模式、預設值。

路徑一律從「程式自己的位置」推算，不寫死 C 槽（第 3 批起）：
    <安裝資料夾>\\
      app\\        ← 這份程式（APP_DIR）
      python\\     ← 安裝程式放的 Python（嵌入式，可攜）
      venv\\       ← 舊的開發環境（第 0～2 批用 setup.ps1 裝的）
      ffmpeg\\bin\\ ← 安裝程式放的 FFmpeg
      songs\\  models\\  settings.json  app.log
所以整個資料夾搬到別的磁碟或隨身碟也能用。
可用環境變數 DENKI_KARAOKE_HOME 覆蓋（雲端測試就是用這個指到暫存資料夾）。
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ["DENKI_KARAOKE_HOME"]) if os.environ.get("DENKI_KARAOKE_HOME") else APP_DIR.parent
SONGS_DIR = ROOT / "songs"
MODELS_DIR = ROOT / "models"   # audio-separator 的模型放這裡（Demucs 用自己的快取）
FFMPEG_DIR = ROOT / "ffmpeg" / "bin"
UPDATE_DIR = ROOT / "_update"  # 程式內更新的暫存與舊版備份

# GitHub（公開 repo）：程式內更新只看「正式發佈」的版本標籤 vX.Y.Z
GITHUB_REPO = "hansonfan77-oss/denki-karaoke"

# ---------------------------------------------------------------- 分離模式
# 每首歌可以同時保留多種模式的分離結果，介面上即時切換比較。
#   backend：demucs / roformer（audio-separator）
#   gpu_only：CPU 上太慢（實測 8 秒片段要 3 分鐘），沒顯卡時不開放
#   tag：輸出檔名的標記，避免不同模式的成品互相覆蓋
MODES: dict[str, dict] = {
    "standard": {
        "label": "標準", "backend": "demucs", "model": "htdemucs_ft",
        "gpu_only": False, "tag": "",
        "desc": "Demucs，速度快，沒有顯卡也能用",
    },
    "hq": {
        "label": "高品質", "backend": "roformer", "model": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "gpu_only": True, "tag": "_高品質", "size_mb": 610,
        "desc": "BS-RoFormer，樂器保留最完整",
    },
    "harmony": {
        "label": "保留和聲", "backend": "roformer", "model": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        "gpu_only": True, "tag": "_保留和聲", "size_mb": 871,
        "desc": "只去掉主唱，和聲留在伴奏裡",
    },
}
MODE_ORDER = ["standard", "hq", "harmony"]

# 分離結果的「版本」。修正了會讓結果壞掉的問題時就加 1，舊版做出來的高品質／保留和聲結果會自動重做。
#   2：rotary-embedding-torch 鎖 0.6.5（之前裝到 0.9.x 的電腦，RoFormer 人聲全是零）
SEP_REV = 2
MIN_SEP_REV = {"hq": 2, "harmony": 2}

# 相容舊程式：命令列 --model 預設值
DEFAULT_MODEL = MODES["standard"]["model"]

STEM_EXT = ".flac"   # 中間檔存無損 FLAC（音質跟 WAV 一樣、空間約一半）

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi", ".flv"}
SUPPORTED_EXTS = AUDIO_EXTS | VIDEO_EXTS

# 分離前統一轉成這個規格，避免各種來源格式讓模型出問題
WORK_SAMPLE_RATE = 44100
WORK_CHANNELS = 2

# 參數範圍（介面與命令列共用）
KEY_MIN, KEY_MAX = -12, 12
GUIDE_MIN, GUIDE_MAX = 0, 100
TEMPO_MIN, TEMPO_MAX = 50, 150

# 音量補償：去掉人聲後把伴奏拉回原曲響度，最多補這麼多 dB
MAX_COMPENSATION_DB = 12.0

MP3_QUALITY = "2"        # libmp3lame -q:a，2 約 190kbps VBR
VIDEO_AUDIO_BITRATE = "192k"
