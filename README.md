# DENKI 伴奏工具

把一首歌（音檔或影片）丟進去 → AI 去人聲 → 調 Key、調導唱 → 輸出伴唱檔。
之後會加上卡拉OK 字幕影片（第二期）。

完整規格、架構與交接備忘在 Notion：「AI 開發專案管理」→「DENKI 伴奏工具」。

## 資料夾配置（Windows）

```
C:\denki-karaoke\
  app\        ← 這個 repo（程式）
  venv\       ← Python 3.11 環境（setup.ps1 建立）
  songs\      ← 每首歌一個資料夾：中間檔 stems\ 與輸出 out\
  結果.txt    ← 最近一次自動測試的結果
```

## 第一次安裝

1. 把這個 repo 放到 `C:\denki-karaoke\app`
2. 右鍵 `setup.ps1` → 用 PowerShell 執行（或在 PowerShell 貼：`powershell -ExecutionPolicy Bypass -File C:\denki-karaoke\app\setup.ps1`）
3. 點兩下 `更新並測試.bat`，看 `結果.txt` 是不是全部通過

## 怎麼用

**桌面程式（第 2 批）：** 點桌面上的「DENKI 伴奏工具」捷徑（或 `開啟伴奏工具.bat`）。

1. 拖一首歌進視窗（或按「選擇檔案」）
2. 等 AI 分析（每首只跑一次，之後從「最近處理」直接進）
3. 邊聽邊調：Key 加減、導唱滑桿、速度，全部即時生效；波形上拖曳可框出 A-B 重複段落；空白鍵播放／暫停
4. 右上角「分離模式」可以切換比較（沒分析過的模式會就地分析，之後秒切）
5. 一開始就進歌的曲子：勾「預備拍」（鼓棒互敲，1 或 2 小節），可以試聽、微調第一拍、點拍抓速度
6. 選格式 →「輸出這個版本」（音量補償預設開啟）

### 預備拍（2.3）

- 速度與第一拍在勾選時才偵測（librosa，看伴奏軌前 90 秒），結果存在 `project.json` 的 `beats`，手動修正也存在那裡
- 只加在音檔（MP3／WAV）；影片維持原長度，但「同時輸出 MP3」的 MP3 會加
- 時間公式（輸出與預覽相同）：一拍 = 60 / BPM / 速度；前面補靜音 = max(0, 拍數 × 一拍 − 第一拍)；最後一下鼓棒之後剛好一拍接上第一拍
- 快歌慢歌可能被抓成兩倍或一半速度，介面有 ÷2／×2
- 命令列：`--countin 1`

### 分離模式

| 模式 | 模型 | 說明 |
|---|---|---|
| 標準 | Demucs htdemucs_ft | 快，沒有顯卡也能用 |
| 高品質 | BS-RoFormer（ep_317，SDR 12.97） | 預設（有顯卡時），樂器誤判最少 |
| 保留和聲 | Mel-RoFormer 卡拉OK（aufr33 & viperx） | 只去主唱，和聲留在伴奏；導唱滑桿控制主唱 |

高品質與保留和聲只開放給有 NVIDIA 顯卡的電腦（CPU 上實測 8 秒片段要 3 分鐘）。第一次使用會下載模型（約 610 MB／870 MB）到 `C:\denki-karaoke\models`。

### 中間檔

每首歌每個模式約 40 MB（FLAC）。「最近處理」可以逐首或全部清除，輸出的成品不會被刪。

**一問一答（第 1 批）：** 把歌曲檔拖到 `拖檔案到這裡.bat` 上。

**命令列（在 app 資料夾開 PowerShell）：**

```
C:\denki-karaoke\venv\Scripts\python.exe -m karaoke info
C:\denki-karaoke\venv\Scripts\python.exe -m karaoke make  "歌.mp4" --key -3 --guide 20 --mp3
C:\denki-karaoke\venv\Scripts\python.exe -m karaoke render "歌名" --key 5          ← 不重跑 AI，幾秒完成
C:\denki-karaoke\venv\Scripts\python.exe -m karaoke list
```

| 參數 | 意思 |
|---|---|
| `--key` | 升降幾個半音，-12 到 +12。女轉男常用 -5，男轉女常用 +5 |
| `--guide` | 保留多少原唱當導唱，0–100% |
| `--format` | `auto`（影片出影片、音檔出 mp3）/ `video` / `mp3` / `wav` |
| `--mp3` | 影片之外同時輸出 MP3 |
| `--tempo` | 速度 %，練習用；影片輸出時忽略 |
| `--countin` | 預備拍小節數 0／1／2（鼓棒，只加在 MP3／WAV） |
| `--out` | 輸出到指定資料夾 |

## 程式結構

```
karaoke/
  config.py     路徑、格式、參數範圍
  hardware.py   顯卡偵測（沒有就 CPU 模式）
  ffmpeg.py     FFmpeg 包裝（一律參數陣列，中日文檔名安全）
  project.py    每首歌的資料夾與 project.json（stems/<模式>/、舊資料自動轉成「標準」模式）
  separate.py   人聲分離（demucs / roformer / 測試用 fake），輸出轉 FLAC
  mix.py        導唱混音 + 變調 + 變速 + 音量補償（一條 FFmpeg 指令）
  beats.py      預備拍：偵測速度與第一拍、點拍、合成鼓棒聲、接在音軌前面
  export.py     影片只換音軌 / MP3 / WAV、檔名規則
  pipeline.py   對外只有 analyze() 與 render()
  cli.py        命令列
  server.py     本機後端（FastAPI，只聽 127.0.0.1）
  app.py        桌面視窗（pywebview / Edge WebView2）
ui/             介面原始碼（React + Vite + TypeScript）
  dist/         編譯好的介面（已放進 repo，Windows 端不需要 Node）
  src/audio/engine.ts   預覽播放引擎：Signalsmith Stretch 即時變調
tests/selftest.py   合成音自動測試 + 真實歌曲測試 + 介面檢查
```

### 修改介面

在有 Node 的電腦上：`cd ui && npm install && npm run build`，把 `ui/dist` 一起 commit。
開發時：先跑 `python -m karaoke.server 8765`，再 `npm run dev` 用瀏覽器開。

## 必守的坑

1. Python 鎖 3.11（3.13 上 Demucs 裝不完整）
2. requirements 明列 numpy（Demucs 4.1.0 漏列）
3. PowerShell 中文腳本存 UTF-8 with BOM
4. FFmpeg 用 winget `Gyan.FFmpeg`（含 rubberband 變調濾鏡）
5. amix 一定要 `normalize=0`，否則音量減半
6. 所有外部指令用參數陣列，不拼字串
7. Signalsmith Stretch **不能被 Vite 打包**（它把自己的原始碼送進音訊執行緒，壓縮改名後會卡住），放在 `ui/public/vendor/` 執行時載入
8. Stretch 節點的 `numberOfInputs` 要設 1（設 0 會沒聲音）
9. pywebview 6 的對話框常數是 `FileDialog.OPEN / FOLDER`；檔案類型用分號分隔
10. pywebview 的 DOM 拖放掛勾曾讓視窗沒回應，預設關閉（`DENKI_DROP_HOOK=1` 才開），拖放改走上傳
11. **audio-separator 要用 `--no-deps` 裝**：它的相依 diffq 沒有 Python 3.11 Windows 版（會要求 C++ 編譯器），而 RoFormer 用不到 diffq；也避免它把顯卡版 PyTorch 換成 CPU 版。其餘相依列在 `requirements-roformer.txt`，另外要 onnxruntime（`[cpu]` extra）
12. 音量補償 = 原曲 LUFS − 伴奏 LUFS（上限 12 dB），再乘上（1 − 導唱%）；輸出與預覽都有限幅器防破音
13. **不要裝 onnx2torch-py313 / torchvision**：onnx2torch 要求 torchvision，pip 從一般來源補裝時會把顯卡版 PyTorch 換成 CPU 版（2.1 版踩過）。RoFormer 用不到它們。更新腳本會自動移除，並在「有 NVIDIA 顯卡但 PyTorch 是 CPU 版」時自動重裝顯卡版
14. 2.1 版以前分析的歌沒有響度資料，第一次打開或輸出時自動補量（`pipeline.ensure_loudness`），否則音量補償會是 0
