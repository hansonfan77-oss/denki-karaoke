# DENKI 伴奏工具

把一首歌（音檔或影片）丟進去 → AI 去人聲 → 調 Key、調導唱 → 輸出伴唱檔。
第二期：卡拉OK 字幕影片（第 4 批：抓歌詞與對時間；第 5 批：字幕樣式與輸出 MP4）。

完整規格、架構與交接備忘在 Notion：「AI 開發專案管理」→「DENKI 伴奏工具」。

## 資料夾配置（Windows）

```
<安裝位置>\            預設 C:\denki-karaoke，整個資料夾搬到別的磁碟也能用（路徑都從程式位置推算）
  app\                ← 這個 repo（程式）；程式內更新只換這個資料夾
  python\             ← Python 3.11.9 嵌入式版本（安裝程式放的，不動到電腦上其他 Python）
  venv\               ← 舊的開發環境（第 0～2 批），有 python\ 之後就用不到
  ffmpeg\bin\         ← FFmpeg（含 rubberband）
  models\             ← AI 模型（RoFormer 在這裡，Demucs 在 models\torch，對時間的 Whisper 在 models\whisper，第一次用到才下載）
  songs\              ← 每首歌一個資料夾：中間檔 stems\、輸出 out\、歌詞 lyrics.json（清除中間檔不會刪）
  _update\            ← 程式內更新的暫存、舊版備份（backup\）、update.log
  結果.txt  app.log  install-log.txt  解除安裝.bat
```

## 安裝（第 3 批起）

1. 解壓縮「DENKI伴奏工具-vX.Y.Z-安裝.zip」，點兩下 `安裝.bat`（跳出 Windows 保護畫面就按「其他資訊 → 仍要執行」）
2. 選安裝位置（預設 C:\denki-karaoke）→ 自動裝 Python、FFmpeg、程式、PyTorch（有 NVIDIA 顯卡裝顯卡版）、其他套件 → 自我檢查 → 建桌面與開始選單捷徑
3. 中途失敗再點一次會從中斷的地方繼續；完整紀錄在 `install-log.txt`、`install-pip-log.txt`

安裝程式本體在 `installer/install.ps1`；打包成 zip：`python installer/build_package.py`。

## 更新

- **正式版本：** 在 GitHub 發佈 Release（標籤 `vX.Y.Z`，要跟 `karaoke/__init__.py` 的版本號一樣；說明欄就是介面上「這次改了什麼」）。
  每台電腦開啟時會自動檢查，右上角出現「有新版本」→ 立刻更新 → 下載 → 關閉 → 更新小幫手換版、補套件、檢查 → 重新開啟。
  失敗會自動換回舊版。草稿與「預先發佈」不會推給店裡的電腦。
- **套件：** 全部照 `requirements-lock.txt` 的確切版本、`--no-deps` 安裝（`python -m karaoke.deps`），PyTorch 版本在 `karaoke/deps.py`。
  要升級套件：先在一台電腦驗證（更新並測試.bat 全過、實際分離一首歌人聲正常），再改鎖定清單。
- **手動測試版：** 把新的 app 覆蓋上去，點 `更新並測試.bat`（照鎖定清單補套件 + 自動測試）。

## 怎麼用

**桌面程式（第 2 批）：** 點桌面上的「DENKI 伴奏工具」捷徑（或 `開啟伴奏工具.bat`）。

1. 拖一首歌進視窗（或按「選擇檔案」）
2. 等 AI 分析（每首只跑一次，之後從「最近處理」直接進）
3. 邊聽邊調：Key 加減、導唱滑桿、速度，全部即時生效；波形上拖曳可框出 A-B 重複段落；空白鍵播放／暫停
4. 右上角「分離模式」可以切換比較（沒分析過的模式會就地分析，之後秒切）
5. 一開始就進歌的曲子：勾「預備拍」（鼓棒互敲，1 或 2 小節），可以試聽、微調第一拍、點拍抓速度
6. 選格式 →「輸出這個版本」（音量補償預設開啟）

### 卡拉影片（第二期，第 4 批）

上方切到「卡拉影片」分頁：

1. 左邊選一首分析過的歌（對時間要用到分離出的人聲）
2. 歌詞三種來源：**LRCLIB 搜尋**（歌名從檔名猜好）、**貼上歌詞**（AI 聽人聲自動對時間；也可以不用 AI 自己點）、**匯入 LRC 檔**
3. 對時間：整體前後移滑桿（±10 秒）＋單句 ±0.1 秒、「設成現在」；播放中按空白鍵＝把選取的句子設成現在並跳下一句；Ctrl+Z 復原；自動儲存
4. 字幕樣式與輸出 MP4 是第 5 批

AI 對時間用 stable-ts（Whisper medium，約 1.5 GB，第一次用到才下載到 models\whisper）。

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

高品質與保留和聲只開放給有 NVIDIA 顯卡的電腦（CPU 上實測 8 秒片段要 3 分鐘）。第一次使用會下載模型（約 610 MB／870 MB）到 `<安裝位置>\models`。

### 中間檔

每首歌每個模式約 40 MB（FLAC）。「最近處理」可以逐首或全部清除，輸出的成品不會被刪。

**一問一答（第 1 批）：** 把歌曲檔拖到 `拖檔案到這裡.bat` 上。

**命令列（在 app 資料夾開 PowerShell）：**

```
..\python\python.exe -m karaoke info
..\python\python.exe -m karaoke make  "歌.mp4" --key -3 --guide 20 --mp3
..\python\python.exe -m karaoke render "歌名" --key 5          ← 不重跑 AI，幾秒完成
..\python\python.exe -m karaoke list
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
  deps.py       照鎖定清單安裝／核對套件（安裝程式、更新、更新並測試共用）
  updater.py    程式內更新：問 GitHub Releases、下載核對、交給小幫手
  update_helper.py  更新小幫手（只用內建功能）：換版、補套件、檢查、失敗換回、重新開啟
  lyrics.py     卡拉影片的歌詞：LRCLIB 搜尋、LRC 解析／輸出、lyrics.json、AI 逐字時間對應回每一句
  align.py      AI 對時間：下載 Whisper 模型（核對 SHA-256）、在子程序跑 align_worker.py
installer/      安裝程式（install.ps1、uninstall.ps1、安裝.bat、圖示、打包腳本）
wheels/         PyPI 上只有原始碼的套件，預先打包（proxy-tools、openai-whisper、stable-ts）
ui/             介面原始碼（React + Vite + TypeScript）
  dist/         編譯好的介面（已放進 repo，Windows 端不需要 Node）
  src/audio/engine.ts   預覽播放引擎：Signalsmith Stretch 即時變調
  src/karaoke/  卡拉影片分頁（lyricsView.ts＝字幕顯示規則：兩行交替、提前顯示、間奏倒數、整句均分掃色）
tests/selftest.py   合成音自動測試 + 真實歌曲測試 + 介面檢查 + 安裝與更新檢查（歌詞與對時間在 lyrics_selftest.py）
```

### 修改介面

在有 Node 的電腦上：`cd ui && npm install && npm run build`，把 `ui/dist` 一起 commit。
開發時：先跑 `python -m karaoke.server 8765`，再 `npm run dev` 用瀏覽器開。

## 必守的坑

1. Python 鎖 3.11（3.13 上 Demucs 裝不完整）
2. requirements 明列 numpy（Demucs 4.1.0 漏列）
3. PowerShell 中文腳本存 UTF-8 with BOM
4. FFmpeg 用 Gyan 的版本（含 rubberband 變調濾鏡），安裝程式放在 `ffmpeg\bin`
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
15. **套件一律鎖確切版本**：rotary-embedding-torch 0.9.x 讓 RoFormer 人聲全零（2.3.3 踩過），`requirements-lock.txt` 鎖 0.6.5；測試要驗「內容」（人聲軌有聲音），不能只看檔案在不在
16. pywebview 的 js_api 物件只能有方法，公開欄位（例如 window）會讓它每次載入頁面翻遍 .NET 物件 → 視窗沒有回應（2.3.3）
17. PyTorch、librosa 放子程序載入（載入時佔住 GIL，視窗會卡）；所有子程序加 CREATE_NO_WINDOW
18. 嵌入式 Python 的 `python311._pth` 要有 `import site` 和 `..\app`，不然找不到套件和程式
19. 更新小幫手只能用 Python 內建功能，而且要在 `_update` 裡執行（待在 app 資料夾裡，Windows 就換不掉它）
20. 發佈的標籤版本要跟程式裡的版本號一樣，不一樣會拒絕更新（不然會一直提示有新版）
