/**
 * 預覽播放引擎（③ 邊聽邊調）
 *
 * 伴奏 L/R + 人聲 L/R 四個聲道放進同一個 Signalsmith Stretch 節點（即時變調／變速，
 * 兩軌永遠同步），出來之後再拆開：人聲兩個聲道經過「導唱」音量，最後混回立體聲。
 *
 *   stretch(4ch) → splitter ─ 0 ───────────────→ merger L
 *                           ├ 1 ───────────────→ merger R
 *                           ├ 2 → 導唱音量 L ──→ merger L
 *                           └ 3 → 導唱音量 R ──→ merger R → 補償音量 → 限幅器 → 喇叭
 *
 * 預覽音質比最後輸出（FFmpeg rubberband 離線高品質）略差，但足夠判斷 Key 對不對。
 */
import type { StretchNode } from 'signalsmith-stretch'
import { stemUrl, type ModeId } from '../api'

/**
 * 注意：Signalsmith Stretch 會把自己的函式原始碼轉成字串送進 AudioWorklet 執行緒，
 * 被 Vite 打包壓縮後變數改名就對不上（會卡住不動）。所以不打包它，
 * 放在 public/vendor/ 原封不動，執行時才動態載入。
 */
type StretchFactory = (ctx: BaseAudioContext, options?: AudioWorkletNodeOptions) => Promise<StretchNode>
let factory: Promise<StretchFactory> | null = null
function loadStretch(): Promise<StretchFactory> {
  const url = new URL('vendor/SignalsmithStretch.mjs', document.baseURI).href
  factory ??= import(/* @vite-ignore */ url).then(m => m.default as StretchFactory)
  return factory
}

export interface LoadProgress {
  stage: 'download' | 'decode' | 'prepare'
  ratio: number
}

export class PreviewEngine {
  readonly ctx: AudioContext
  readonly duration: number
  readonly peaks: Float32Array      // 波形：每個點 0~1
  readonly vocalPeaks: Float32Array // 人聲波形（找副歌、畫導唱）
  private node: StretchNode
  private guideL: GainNode
  private guideR: GainNode
  private master: GainNode
  private out: AudioNode
  private sticks: AudioBuffer | null = null
  private clickSources: AudioBufferSourceNode[] = []
  readonly mode: ModeId
  private _playing = false
  private _pos = 0
  private _key = 0
  private _rate = 1
  private _loop: [number, number] | null = null
  private _disposed = false
  private endTimer: number | undefined
  onEnded?: () => void

  private constructor(ctx: AudioContext, node: StretchNode, duration: number,
    peaks: Float32Array, vocalPeaks: Float32Array,
    guideL: GainNode, guideR: GainNode, master: GainNode, out: AudioNode, mode: ModeId) {
    this.ctx = ctx
    this.out = out
    this.mode = mode
    this.node = node
    this.duration = duration
    this.peaks = peaks
    this.vocalPeaks = vocalPeaks
    this.guideL = guideL
    this.guideR = guideR
    this.master = master
    node.setUpdateInterval(0.05)
    this.endTimer = window.setInterval(() => this.checkEnd(), 200)
  }

  static async load(slug: string, mode: ModeId, onProgress?: (p: LoadProgress) => void): Promise<PreviewEngine> {
    const ctx = new AudioContext({ sampleRate: 44100, latencyHint: 'interactive' })
    const report = (stage: LoadProgress['stage'], ratio: number) => onProgress?.({ stage, ratio })

    const [nvBuf, vBuf] = await Promise.all([
      fetchWithProgress(stemUrl(slug, mode, 'no_vocals'), r => report('download', r * 0.5)),
      fetchWithProgress(stemUrl(slug, mode, 'vocals'), r => report('download', 0.5 + r * 0.5)),
    ])
    report('decode', 0)
    const [nv, v] = await Promise.all([ctx.decodeAudioData(nvBuf), ctx.decodeAudioData(vBuf)])
    report('prepare', 0)

    const len = Math.min(nv.length, v.length)
    const ch = (b: AudioBuffer, i: number) =>
      b.getChannelData(Math.min(i, b.numberOfChannels - 1)).subarray(0, len)
    const nvL = ch(nv, 0), nvR = ch(nv, 1), vL = ch(v, 0), vR = ch(v, 1)

    // 先算波形：addBuffers 可能把資料轉移到音訊執行緒，之後這邊就讀不到了
    const bins = 1200
    const peaks = computePeaks([nvL, nvR, vL, vR], bins)
    const vocalPeaks = computePeaks([vL, vR], bins)

    const SignalsmithStretch = await loadStretch()
    const node = await SignalsmithStretch(ctx, {
      // 輸入要留 1 個（不接東西）：設成 0 時處理器會在播放時出錯、沒有聲音
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [4],
    })
    await node.addBuffers([nvL, nvR, vL, vR])

    const splitter = ctx.createChannelSplitter(4)
    const merger = ctx.createChannelMerger(2)
    const guideL = ctx.createGain()
    const guideR = ctx.createGain()
    const master = ctx.createGain()
    node.connect(splitter)
    splitter.connect(merger, 0, 0)
    splitter.connect(merger, 1, 1)
    splitter.connect(guideL, 2)
    splitter.connect(guideR, 3)
    guideL.connect(merger, 0, 0)
    guideR.connect(merger, 0, 1)
    // 限幅器：音量補償把伴奏拉大之後，避免峰值破音（跟輸出時的 alimiter 對應）
    const limiter = ctx.createDynamicsCompressor()
    limiter.threshold.value = -1
    limiter.knee.value = 0
    limiter.ratio.value = 20
    limiter.attack.value = 0.003
    limiter.release.value = 0.1
    merger.connect(master)
    master.connect(limiter)
    limiter.connect(ctx.destination)
    report('prepare', 1)
    return new PreviewEngine(ctx, node, len / nv.sampleRate, peaks, vocalPeaks, guideL, guideR, master, limiter, mode)
  }

  // ---------------- 狀態
  get playing() { return this._playing }
  get position(): number {
    return this._playing ? Math.min(Math.max(this.node.inputTime, 0), this.duration) : this._pos
  }
  get key() { return this._key }
  get rate() { return this._rate }
  get loop() { return this._loop }

  // ---------------- 操作
  async play() {
    if (this._disposed) return
    await this.ctx.resume()
    let from = this._pos
    if (from >= this.duration - 0.05) from = this._loop ? this._loop[0] : 0
    this.node.schedule({
      output: this.ctx.currentTime, active: true, input: from,
      rate: this._rate, semitones: this._key, ...this.loopFields(),
    })
    this._playing = true
  }

  /**
   * 從預備拍開始試聽：先敲 bars×4 下鼓棒，最後一下之後剛好一拍接上歌曲第一拍。
   * 時間計算跟輸出（karaoke/beats.py 的 plan）完全一樣，也跟著速度滑桿縮放。
   * 回傳預備拍總長（秒），介面用來顯示倒數。
   */
  async playWithCountIn(bpm: number, firstBeat: number, bars: number): Promise<number> {
    if (this._disposed) return 0
    await this.ctx.resume()
    if (this._playing) this.pause()
    this.stopClicks()
    const sticks = await this.loadSticks()
    const period = 60 / bpm / this._rate
    const first = Math.max(0, firstBeat) / this._rate
    const n = bars * 4
    const pad = Math.max(0, n * period - first)
    const t0 = this.ctx.currentTime + 0.12       // 留一點排程時間，第一下才不會被吃掉
    for (let k = n; k >= 1; k--) {
      const src = this.ctx.createBufferSource()
      src.buffer = sticks
      const g = this.ctx.createGain()
      g.gain.value = (n - k) % 4 === 0 ? 0.6 : 0.48   // 每小節第一下重一點（跟輸出一樣）
      src.connect(g).connect(this.out)
      src.start(t0 + pad + first - k * period)
      this.clickSources.push(src)
    }
    this._pos = 0
    this.node.schedule({
      output: t0 + pad, active: true, input: 0,
      rate: this._rate, semitones: this._key, loopStart: 0, loopEnd: 0,
    })
    this._playing = true
    return pad + first
  }

  pause() {
    this.stopClicks()
    if (!this._playing) return
    this._pos = this.position
    this.node.schedule({ output: this.ctx.currentTime, active: false })
    this._playing = false
  }

  toggle() { return this._playing ? this.pause() : this.play() }

  seek(t: number) {
    this.stopClicks()
    const to = clamp(t, 0, this.duration)
    this._pos = to
    if (this._playing) this.node.schedule({ output: this.ctx.currentTime, input: to })
  }

  setKey(semitones: number) {
    this._key = semitones
    this.node.schedule({ output: this.ctx.currentTime, semitones })
  }

  setRate(rate: number) {
    this._rate = rate
    this.node.schedule({ output: this.ctx.currentTime, rate })
  }

  /** 導唱 0~1 */
  setGuide(g: number) {
    const t = this.ctx.currentTime
    this.guideL.gain.setTargetAtTime(g, t, 0.02)
    this.guideR.gain.setTargetAtTime(g, t, 0.02)
  }

  /** 音量補償（dB）：去人聲後把伴奏拉回原曲響度 */
  setCompensation(db: number) {
    this.master.gain.setTargetAtTime(Math.pow(10, db / 20), this.ctx.currentTime, 0.05)
  }

  setLoop(loop: [number, number] | null) {
    this._loop = loop && loop[1] - loop[0] > 0.3 ? [loop[0], loop[1]] : null
    this.node.schedule({ output: this.ctx.currentTime, ...this.loopFields() })
    if (this._loop) {
      const p = this.position
      if (p < this._loop[0] || p > this._loop[1]) this.seek(this._loop[0])
    }
  }

  /** 找副歌（估計）：人聲最飽滿的 12 秒，提早 1 秒開始 */
  estimateChorus(): number {
    const win = Math.max(1, Math.round((12 / this.duration) * this.vocalPeaks.length))
    let best = 0, bestAt = 0, sum = 0
    for (let i = 0; i < this.vocalPeaks.length; i++) {
      sum += this.vocalPeaks[i]
      if (i >= win) sum -= this.vocalPeaks[i - win]
      // 略過開頭 10%，避免把前奏人聲當成副歌
      if (i >= win && sum > best && i - win > this.vocalPeaks.length * 0.1) {
        best = sum
        bestAt = i - win + 1
      }
    }
    return Math.max(0, (bestAt / this.vocalPeaks.length) * this.duration - 1)
  }

  dispose() {
    this._disposed = true
    window.clearInterval(this.endTimer)
    try { this.node.schedule({ active: false }) } catch { /* 已經關了 */ }
    this.ctx.close().catch(() => undefined)
  }

  // ---------------- 內部
  private async loadSticks(): Promise<AudioBuffer> {
    if (!this.sticks) {
      const res = await fetch(new URL('api/sticks.wav', document.baseURI).href)
      if (!res.ok) throw new Error('讀不到鼓棒聲')
      this.sticks = await this.ctx.decodeAudioData(await res.arrayBuffer())
    }
    return this.sticks
  }

  private stopClicks() {
    for (const s of this.clickSources) { try { s.stop() } catch { /* 已經播完 */ } }
    this.clickSources = []
  }

  private loopFields() {
    return this._loop ? { loopStart: this._loop[0], loopEnd: this._loop[1] } : { loopStart: 0, loopEnd: 0 }
  }

  private checkEnd() {
    if (this._playing && !this._loop && this.node.inputTime >= this.duration - 0.02) {
      this.pause()
      this._pos = 0
      this.onEnded?.()
    }
  }
}

// ---------------- 工具
function clamp(x: number, lo: number, hi: number) { return Math.min(hi, Math.max(lo, x)) }

function computePeaks(channels: Float32Array[], bins: number): Float32Array {
  const len = channels[0].length
  const out = new Float32Array(bins)
  const step = Math.max(1, Math.floor(len / bins))
  let max = 0
  for (let b = 0; b < bins; b++) {
    const start = b * step
    const end = Math.min(len, start + step)
    let acc = 0
    // 每格取樣 256 點算 RMS，夠準也夠快
    const stride = Math.max(1, Math.floor((end - start) / 256))
    let n = 0
    for (let i = start; i < end; i += stride) {
      let s = 0
      for (const c of channels) s += c[i]
      acc += s * s
      n++
    }
    const rms = Math.sqrt(acc / Math.max(1, n))
    out[b] = rms
    if (rms > max) max = rms
  }
  if (max > 0) for (let b = 0; b < bins; b++) out[b] /= max
  return out
}

async function fetchWithProgress(url: string, onRatio: (r: number) => void): Promise<ArrayBuffer> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`無法讀取中間檔（${res.status}）`)
  const total = Number(res.headers.get('content-length') || 0)
  if (!res.body || !total) {
    const buf = await res.arrayBuffer()
    onRatio(1)
    return buf
  }
  const reader = res.body.getReader()
  const out = new Uint8Array(total)
  let got = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    out.set(value, got)
    got += value.length
    onRatio(got / total)
  }
  return out.buffer
}
