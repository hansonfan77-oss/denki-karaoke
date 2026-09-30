/**
 * 卡拉字幕的「顯示規則」（第 4 批預覽用；第 5 批輸出 MP4 時 Python 端照同一套規則產生字幕）
 *
 * - 畫面兩行交替：第 0、2、4… 句在上面那行，第 1、3、5… 句在下面那行。
 * - 提前顯示：某一句在「上一句開始唱」時就出現（最多提前 LEAD_MAX 秒），歌手有時間先看。
 * - 間奏：兩句之間空檔 ≥ GAP 秒，上一句唱完 HOLD 秒後畫面清空；下一句提前 LEAD_AFTER_GAP 秒出現，
 *   前面 3 秒顯示倒數點點 ●●●（開頭第一句也一樣）。
 * - 掃色：整句均分（從開始到結束等速掃過去），逐字打點之後再做。
 */
import type { LyricLine, RubyItem, WordTime } from '../api'

export const LEAD_MAX = 8        // 一般情況最多提前幾秒出現
export const GAP = 5             // 空檔多長算間奏
export const LEAD_AFTER_GAP = 4  // 間奏後提前幾秒出現
export const HOLD = 1.2          // 唱完之後留多久（間奏前）
export const COUNTDOWN = 3       // 倒數點點秒數

export interface TimedLine { t: number; end: number; text: string; words: WordTime[] | null; ruby: RubyItem[] }

/** 加上整體位移，並補好每一句的結束時間（沒有結束時間：到下一句開始，最長 6 秒）。 */
export function timed(lines: LyricLine[], offset: number): TimedLine[] {
  return lines.map((ln, i) => {
    const t = ln.t + offset
    const next = i + 1 < lines.length ? lines[i + 1].t + offset : Infinity
    let end = ln.end != null ? ln.end + offset : Math.min(next - 0.05, t + 6)
    if (!(end > t)) end = t + 0.5
    const words = ln.words?.length && !ln.even ? ln.words.map(w => ({ ...w, t: w.t + offset, end: w.end + offset })) : null
    return { t, end, text: ln.text, words, ruby: ln.ruby ?? [] }
  })
}

function appear(L: TimedLine[], j: number): number {
  const cur = L[j]
  if (j === 0) return cur.t - LEAD_AFTER_GAP
  const prev = L[j - 1]
  if (cur.t - prev.end >= GAP) return cur.t - LEAD_AFTER_GAP
  return Math.max(prev.t, cur.t - LEAD_MAX)
}

function vanish(L: TimedLine[], j: number): number {
  const cur = L[j]
  const next = L[j + 1]
  if (!next) return cur.end + HOLD + 1
  if (next.t - cur.end >= GAP) return cur.end + HOLD
  const afterNext = L[j + 2]
  // 下一句之後就是間奏（或結尾）：跟下一句一起消失
  if (!afterNext || afterNext.t - next.end >= GAP) return Math.max(cur.end, next.end + HOLD)
  // 這一行的位置要讓給 j+2：j+2 出現時（通常是 j+1 開始唱的時候）
  return Math.max(appear(L, j + 2), cur.end)
}

export interface SlotView {
  index: number        // 第幾句
  text: string
  sweep: number        // 0~1 已唱比例
  active: boolean      // 正在唱
}

export interface StageView {
  slots: [SlotView | null, SlotView | null]  // [上, 下]
  countdown: { slot: 0 | 1; dots: number } | null   // dots = 還剩幾點（3、2、1）
  current: number      // 正在唱（或最近唱過）的句子，-1 = 還沒開始
}

/** 逐字掃色：已唱的比例（照字寬加權，跟畫面上的位置一致） */
function wordSweep(ln: TimedLine, T: number): number {
  const ws = ln.words!
  let total = 0
  const widths = ws.map(w => { let x = 0; for (const ch of w.text) x += charW(ch); total += x; return x })
  if (total <= 0) return 0
  let done = 0
  for (let k = 0; k < ws.length; k++) {
    const w = ws[k]
    if (T >= w.end) { done += widths[k]; continue }
    if (T > w.t) done += widths[k] * ((T - w.t) / Math.max(0.01, w.end - w.t))
    break
  }
  return Math.min(1, done / total)
}

export function stageAt(L: TimedLine[], T: number, wordMode = true): StageView {
  const slots: [SlotView | null, SlotView | null] = [null, null]
  let current = -1
  for (let j = 0; j < L.length; j++) {
    if (L[j].t <= T) current = j
    const a = appear(L, j)
    if (a > T) break
    if (T >= vanish(L, j)) continue
    const ln = L[j]
    const sweep = wordMode && ln.words
      ? wordSweep(ln, T)
      : Math.min(1, Math.max(0, (T - ln.t) / (ln.end - ln.t)))
    slots[j % 2] = { index: j, text: ln.text, sweep, active: T >= ln.t && T < ln.end }
  }
  let countdown: StageView['countdown'] = null
  const nextIdx = current + 1
  if (nextIdx < L.length) {
    const ln = L[nextIdx]
    const gapBefore = nextIdx === 0 ? ln.t : ln.t - L[nextIdx - 1].end
    const left = ln.t - T
    if (gapBefore >= GAP - 0.01 && left > 0 && left <= COUNTDOWN) {
      countdown = { slot: (nextIdx % 2) as 0 | 1, dots: Math.ceil(left) }
    }
  }
  return { slots, countdown, current }
}

// ---------------- 小工具
export function fmtStamp(sec: number): string {
  const neg = sec < 0
  const cs = Math.round(Math.abs(sec) * 100)
  const m = Math.floor(cs / 6000)
  const s = Math.floor(cs / 100) % 60
  return `${neg ? '−' : ''}${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs % 100).padStart(2, '0')}`
}

export function fmtOffset(sec: number): string {
  if (Math.abs(sec) < 0.005) return '0.00 秒'
  return `${sec > 0 ? '+' : '−'}${Math.abs(sec).toFixed(2)} 秒`
}

/** 跟後端 lyrics.detect_language 同一套判斷（只用來顯示） */
export function detectLang(text: string): 'ja' | 'zh' | 'ko' | 'en' {
  let kana = 0, hangul = 0, han = 0
  for (const ch of text) {
    const o = ch.codePointAt(0) ?? 0
    if ((o >= 0x3040 && o <= 0x30ff) || (o >= 0x31f0 && o <= 0x31ff) || (o >= 0xff66 && o <= 0xff9f)) kana++
    else if ((o >= 0xac00 && o <= 0xd7af) || (o >= 0x1100 && o <= 0x11ff)) hangul++
    else if ((o >= 0x4e00 && o <= 0x9fff) || (o >= 0x3400 && o <= 0x4dbf)) han++
  }
  if (kana >= 3 || (kana && kana * 10 >= han)) return 'ja'
  if (hangul >= 3) return 'ko'
  if (han >= 3) return 'zh'
  return 'en'
}

export const LANG_LABEL: Record<string, string> = { ja: '日文', zh: '中文', ko: '韓文', en: '英文' }

export const SOURCE_LABEL: Record<string, string> = {
  lrclib: 'LRCLIB', ai: 'AI 對時間', lrc: '匯入 LRC', manual: '自己對時間',
}

/** 貼上的純文字 → 一行一句（跟後端 split_plain 一樣） */
export function splitPlain(text: string): string[] {
  return text.replace(/\r\n/g, '\n').split('\n')
    .map(l => l.replace(/\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]/g, '').replace(/<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>/g, '').trim())
    .filter(l => l && !/^[[(（【].{0,20}[\])）】]$/.test(l))
}

/**
 * 改了第 i 句的開始時間之後，讓前後句子保持順序：
 * 後面比它早的往後推、前面比它晚的往前拉（至少差 0.3 秒）；結束時間跟著開始時間一起移動（長度不變），
 * 上一句的結束時間不能超過這一句的開始。
 */
const shiftW = (ws: WordTime[] | null | undefined, d: number) =>
  ws?.length ? ws.map(w => ({ ...w, t: Math.round((w.t + d) * 1000) / 1000, end: Math.round((w.end + d) * 1000) / 1000 })) : ws

export function setStart(lines: LyricLine[], i: number, t: number): LyricLine[] {
  const out = lines.map(l => ({ ...l }))
  const old = out[i].t
  t = Math.max(0, Math.round(t * 1000) / 1000)
  const delta = t - old
  out[i].t = t
  out[i].words = shiftW(out[i].words, delta)
  if (out[i].end != null) out[i].end = Math.round((out[i].end! + delta) * 1000) / 1000
  for (let k = i + 1; k < out.length; k++) {
    if (out[k].t > out[k - 1].t + 0.05) break
    const d = out[k - 1].t + 0.3 - out[k].t
    out[k].t = Math.round((out[k].t + d) * 1000) / 1000
    out[k].words = shiftW(out[k].words, d)
    if (out[k].end != null) out[k].end = Math.round((out[k].end! + d) * 1000) / 1000
  }
  for (let k = i - 1; k >= 0; k--) {
    if (out[k].t < out[k + 1].t - 0.05) break
    const d = out[k].t - (out[k + 1].t - 0.3)
    out[k].t = Math.max(0, Math.round((out[k].t - d) * 1000) / 1000)
    out[k].words = shiftW(out[k].words, -d)
    if (out[k].end != null) out[k].end = Math.round((out[k].end! - d) * 1000) / 1000
  }
  for (let k = 0; k < out.length - 1; k++) {
    const e = out[k].end
    if (e != null && e > out[k + 1].t - 0.02) out[k].end = Math.round((out[k + 1].t - 0.02) * 1000) / 1000
    if (out[k].end != null && out[k].end! <= out[k].t) out[k].end = null
  }
  return out
}

/** 沒有時間的歌詞（自己對時間用）：先平均排在歌曲裡，之後用空白鍵一句一句點 */
export function spreadEvenly(texts: string[], duration: number): LyricLine[] {
  const start = Math.min(10, duration * 0.08)
  const span = Math.max(1, (duration - start - 5) / Math.max(1, texts.length))
  return texts.map((text, i) => ({ t: Math.round((start + i * span) * 100) / 100, end: null, text }))
}

// ---------------- 第 5 批：字幕樣式與版面（跟 karaoke/kvideo.py 的 geometry()、fit_size() 同一套數字）
export interface KStyle {
  font: string
  size: number
  unsung: string
  sung: string
  outline: number
  position: 'bottom' | 'middle'
  plate: 'none' | 'black' | 'white'
  plateOpacity: number
  countdown: boolean
  sweep: boolean
  sweepWord: boolean
  furigana: boolean   // 漢字上方標假名（v0.7.0）
  rubySize: number    // 假名大小＝字幕大小的幾 %
}

export const SIZE_MAX = 140

export const VW = 1920, VH = 1080

export function geometry(s: KStyle) {
  const fs = s.size
  const padV = fs * 0.45, padH = Math.max(60, fs * 1.1)
  const dotsH = fs * 0.5, lineH = fs * 1.25, gap = fs * 0.25
  const rubyH = s.furigana ? fs * (s.rubySize ?? 45) / 100 * 1.15 : 0   // 假名那一列
  const row = dotsH + rubyH + lineH
  const plateH = 2 * padV + 2 * row + gap
  const top = s.position === 'bottom' ? VH * 0.96 - plateH : (VH - plateH) / 2
  const slots = [0, 1].map(k => {
    const y0 = top + padV + k * (row + gap)
    return { dots: y0, line: y0 + dotsH + rubyH }
  })
  return { fs, padH, plateTop: top, plateH, lineH, rubyH, slots }
}

function charW(ch: string): number {
  if (ch === ' ') return 0.3
  const o = ch.codePointAt(0) ?? 0
  // 全形（中日韓文字、假名、全形符號）當 1 個字寬，其他 0.58
  if ((o >= 0x1100 && o <= 0x115f) || (o >= 0x2e80 && o <= 0xa4cf) || (o >= 0xac00 && o <= 0xd7a3) ||
    (o >= 0xf900 && o <= 0xfaff) || (o >= 0xfe30 && o <= 0xfe4f) || (o >= 0xff00 && o <= 0xff60) ||
    (o >= 0xffe0 && o <= 0xffe6) || (o >= 0x20000 && o <= 0x3fffd) || o === 0x3000) return 1
  return 0.58
}

/** 太長的句子縮小（不超出畫面） */
export function fitSize(text: string, fs: number, padH: number): number {
  let w = 0
  for (const ch of text) w += charW(ch)
  w *= fs
  const room = VW - 2 * padH
  return w <= room ? fs : Math.max(fs * 0.5, (fs * room) / w)
}

/** 底板要不要出現：有字幕或倒數的時候 */
export function plateVisible(v: StageView): boolean {
  return !!(v.slots[0] || v.slots[1] || v.countdown)
}

// ---------------- 貼上中日對照歌詞時：自動略過中文翻譯、作詞作曲資訊
const CREDIT = /^(作詞|作曲|編曲|作词|编曲|歌|唄|Vocal|Lyrics|Music)\s*[:：／/]/i
/** 漢字（含 々 〆 ヶ）的連續段落 [開始, 結束) */
export function kanjiRuns(text: string): [number, number][] {
  const out: [number, number][] = []
  const re = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々〆ヶ]+/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) out.push([m.index, m.index + m[0].length])
  return out
}

function kanaCount(s: string) { let n = 0; for (const ch of s) { const o = ch.codePointAt(0) ?? 0; if (o >= 0x3040 && o <= 0x30ff) n++ } return n }
function hanCount(s: string) { let n = 0; for (const ch of s) { const o = ch.codePointAt(0) ?? 0; if (o >= 0x4e00 && o <= 0x9fff) n++ } return n }

/**
 * 日文歌詞裡混著中文翻譯時，把翻譯行挑出來：整首大多是有假名的句子，而這一行完全沒有假名、卻有兩個以上漢字。
 * 回傳 { keep: 要送去對時間的句子, dropped: 略過的行數 }；不是日文歌詞就原封不動。
 */
export function japaneseOnly(lines: string[]): { keep: string[]; dropped: number } {
  const withKana = lines.filter(l => kanaCount(l) > 0).length
  if (lines.length < 4 || withKana < lines.length * 0.3) return { keep: lines, dropped: 0 }
  const keep = lines.filter(l => !CREDIT.test(l) && !(kanaCount(l) === 0 && hanCount(l) >= 2))
  return { keep, dropped: lines.length - keep.length }
}
