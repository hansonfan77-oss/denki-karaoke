/** ③ 樣式與預覽 → 「標音（假名）」分頁：漢字上方標假名的開關、大小，以及逐句檢查／修改讀音 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type LyricLine, type Lyrics, type RubyItem } from '../api'
import { fmtStamp, kanjiRuns, type KStyle } from './lyricsView'

type Chip = { s: number; e: number; r: string; m?: boolean; auto: boolean }

/** 一句的所有漢字格子：已有讀音的項目，加上沒被標到的漢字段（空白＝不標） */
function chipsOf(ln: LyricLine): Chip[] {
  const items = (ln.ruby ?? []).map(x => ({ ...x, auto: false }))
  const extra = kanjiRuns(ln.text)
    .filter(([s, e]) => !items.some(x => x.s < e && s < x.e))
    .map(([s, e]) => ({ s, e, r: '', auto: true }))
  return [...items, ...extra].sort((a, b) => a.s - b.s)
}

export default function FuriganaPanel({ slug, lyrics, style, onStyle, onLyrics, onSeek, pos }: {
  slug: string
  lyrics: Lyrics
  style: KStyle
  onStyle: (p: Partial<KStyle>) => void
  onLyrics: (l: Lyrics) => void
  onSeek: (t: number) => void
  pos: number
}) {
  const [onlyKanji, setOnlyKanji] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const draft = useRef<Lyrics | null>(null)
  const timer = useRef<number | undefined>(undefined)

  // ---------------- 存檔（改讀音後 0.6 秒自動存）
  const flush = useCallback(async () => {
    window.clearTimeout(timer.current)
    const d = draft.current
    if (!d) return
    draft.current = null
    try {
      await api.saveLyrics(slug, { lines: d.lines, offset: d.offset, source: d.source, lang: d.lang, lrclib: d.lrclib ?? null })
    } catch (e) {
      setErr(`讀音存檔失敗：${(e as Error).message}`)
    }
  }, [slug])
  useEffect(() => () => { flush() }, [flush])

  const setLine = (i: number, ruby: RubyItem[]) => {
    const next = { ...lyrics, lines: lyrics.lines.map((l, k) => (k === i ? { ...l, ruby } : l)) }
    onLyrics(next)
    draft.current = next
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(flush, 600)
  }

  // ---------------- 自動標音（還沒產生讀音的句子；redo＝全部重標，手改的保留）
  const missing = style.furigana && lyrics.lines.some(l => l.ruby == null && kanjiRuns(l.text).length)
  const generate = useCallback(async (redo: boolean) => {
    setBusy(redo ? '重新自動標音中…' : '自動標音中…（第一次要載入日文字典，約幾秒）')
    setErr(null)
    try {
      await flush()
      onLyrics(await api.furigana(slug, redo))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }, [slug, flush, onLyrics])
  const tried = useRef(false)
  useEffect(() => {
    if (missing && !busy && !tried.current) { tried.current = true; generate(false) }
    if (!missing) tried.current = false
  }, [missing, busy, generate])

  const edit = (i: number, chip: Chip, r: string) => {
    const ln = lyrics.lines[i]
    const others = (ln.ruby ?? []).filter(x => !(x.s === chip.s && x.e === chip.e))
    setLine(i, [...others, { s: chip.s, e: chip.e, r: r.trim(), m: true }].sort((a, b) => a.s - b.s))
  }

  const now = (() => {
    let cur = -1
    lyrics.lines.forEach((l, k) => { if (l.t + lyrics.offset <= pos) cur = k })
    return cur
  })()

  const rows = lyrics.lines.map((ln, i) => ({ ln, i })).filter(({ ln }) => !onlyKanji || kanjiRuns(ln.text).length)

  return (
    <div className="kfuri">
      <div className="kfuri-head">
        <div className="kfuri-row">
          <label className="check kfuri-on">
            <input type="checkbox" checked={style.furigana} onChange={e => onStyle({ furigana: e.target.checked })} />漢字標假名
          </label>
          <div className="spacer" />
          <span className="small muted">假名大小</span>
          <input type="range" min={30} max={60} step={5} value={style.rubySize} disabled={!style.furigana}
            aria-label="假名大小" onChange={e => onStyle({ rubySize: Number(e.target.value) })} />
          <b className="small">{style.rubySize}%</b>
        </div>
        {lyrics.lang !== 'ja' && (
          <div className="notice warn small">這首歌的歌詞看起來不是日文，通常不需要標假名。</div>
        )}
        <div className="kt-tip small">
          讀音是自動標的，大約八九成對。歌詞有特殊唱法（例如 本気→マジ、運命→さだめ）直接點假名修改；清空＝那個字不標。
        </div>
        <div className="kfuri-row">
          <label className="check small"><input type="checkbox" checked={onlyKanji} onChange={e => setOnlyKanji(e.target.checked)} />只顯示有漢字的句子</label>
          <div className="spacer" />
          <button className="btn sm" disabled={!!busy || !style.furigana} onClick={() => generate(true)}
            title="重新自動產生所有讀音；你手改過的格子會保留">全部重新自動標音</button>
        </div>
        {busy && <div className="notice warn small">{busy}</div>}
        {err && <div className="notice error small">{err}</div>}
      </div>

      <div className={`kfuri-list ${style.furigana ? '' : 'off'}`}>
        {rows.map(({ ln, i }) => {
          const chips = chipsOf(ln)
          const manual = (ln.ruby ?? []).some(x => x.m)
          return (
            <div key={i} className={`kfuri-line ${i === now ? 'now' : ''}`}>
              <button className="kfuri-text" onClick={() => onSeek(ln.t + lyrics.offset - 1)} title="預覽跳到這句">
                <span className="kfuri-stamp">{fmtStamp(ln.t + lyrics.offset)}</span>
                <span className="kfuri-jp">{ln.text}</span>
                {manual && <span className="kfuri-tag">已手改</span>}
              </button>
              {chips.length > 0 && (
                <div className="kfuri-chips">
                  {chips.map(c => (
                    <label key={`${c.s}-${c.e}`} className={`kfuri-chip ${c.m ? 'm' : ''} ${!c.r ? 'empty' : ''}`}>
                      <span className="kfuri-kanji">{ln.text.slice(c.s, c.e)}</span>
                      <input value={c.r} placeholder="不標" aria-label={`${ln.text.slice(c.s, c.e)} 的讀音`}
                        disabled={!style.furigana || ln.ruby == null}
                        onChange={e => edit(i, c, e.target.value)} />
                    </label>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
