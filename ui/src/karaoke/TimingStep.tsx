/** ② 對時間：邊播邊看字幕，整體前後移 + 單句微調（±0.1 秒、設成現在時間、空白鍵一句一句點） */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, fmtTime, type LyricLine, type Lyrics, type Song } from '../api'
import { PreviewEngine, type LoadProgress } from '../audio/engine'
import { Pause, Play } from '../icons'
import LyricsStage from './LyricsStage'
import { fmtOffset, fmtStamp, setStart, SOURCE_LABEL, stageAt, timed } from './lyricsView'

const OFFSET_MAX = 60   // 找到的歌詞版本跟影片差很多（例如 MV 有長前奏）時也夠用
const UNDO_MAX = 60

type Snapshot = { lines: LyricLine[]; offset: number }

export default function TimingStep({ song, initial, onBack, onSaved, onNext }: {
  song: Song
  initial: Lyrics
  onBack: () => void
  onSaved: (l: Lyrics) => void
  onNext?: () => void
}) {
  const slug = song.slug
  const [lines, setLines] = useState<LyricLine[]>(initial.lines)
  const [offset, setOffset] = useState(initial.offset || 0)
  const [sel, setSel] = useState(0)
  const [editing, setEditing] = useState<{ i: number; text: string } | null>(null)
  const [engine, setEngine] = useState<PreviewEngine | null>(null)
  const [loadP, setLoadP] = useState<LoadProgress | null>(null)
  const [engineErr, setEngineErr] = useState<string | null>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [guide, setGuide] = useState(100)
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'dirty' | 'error'>('saved')
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [stageW, setStageW] = useState(640)
  const undo = useRef<Snapshot[]>([])
  const leftRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const state = useRef({ lines, offset, sel, pos })
  state.current = { lines, offset, sel, pos }

  // ---------------- 預覽引擎（原 Key、原速；導唱＝原唱音量，預設 100% 方便聽歌手什麼時候開口）
  useEffect(() => {
    const mode = song.lastMode ?? song.analyzedModes[0]
    if (!mode) { setEngineErr('這首歌還沒分析，請先到「伴奏處理」分析一次。'); return }
    let cancelled = false
    let made: PreviewEngine | null = null
    PreviewEngine.load(slug, mode, setLoadP)
      .then(e => {
        if (cancelled) return e.dispose()
        made = e
        e.setKey(0)
        e.setRate(1)
        e.setGuide(1)
        e.setCompensation(0)
        e.onEnded = () => setPlaying(false)
        setEngine(e)
      })
      .catch(e => !cancelled && setEngineErr(`播放器載入失敗：${(e as Error).message}`))
    return () => { cancelled = true; made?.dispose() }
  }, [slug]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { engine?.setGuide(guide / 100) }, [engine, guide])

  useEffect(() => {
    if (!engine) return
    let id = 0
    const tick = () => {
      setPos(engine.position)
      setPlaying(engine.playing)
      id = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(id)
  }, [engine])

  // ---------------- 版面：字幕預覽寬度跟著視窗
  useLayoutEffect(() => {
    const el = leftRef.current
    if (!el) return
    // 寬度跟著欄寬，但高度要留空間給下面的播放列與滑桿（約 260px）
    const fit = () => setStageW(Math.max(320, Math.floor(Math.min(el.clientWidth, ((el.clientHeight - 300) * 16) / 9))))
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ---------------- 編輯（每次修改前存一份，可以復原）
  const edit = useCallback((next: Partial<Snapshot>) => {
    const cur = { lines: state.current.lines, offset: state.current.offset }
    undo.current = [...undo.current.slice(-(UNDO_MAX - 1)), cur]
    if (next.lines) setLines(next.lines)
    if (next.offset !== undefined) setOffset(Math.round(Math.max(-OFFSET_MAX, Math.min(OFFSET_MAX, next.offset)) * 100) / 100)
    setSaveState('dirty')
  }, [])

  const doUndo = useCallback(() => {
    const last = undo.current.pop()
    if (!last) return
    setLines(last.lines)
    setOffset(last.offset)
    setSaveState('dirty')
  }, [])

  const moveLine = (i: number, t: number) => edit({ lines: setStart(state.current.lines, i, t) })
  const nudge = (i: number, d: number) => moveLine(i, state.current.lines[i].t + d)
  /** 設成現在播放時間（扣掉整體位移，存的是原始時間） */
  const stampNow = useCallback((i: number) => {
    const { pos: p, offset: o } = state.current
    edit({ lines: setStart(state.current.lines, i, p - o) })
  }, [edit])

  /** 整首一起移：算出位移量，讓選取的句子剛好在「現在」開始（歌詞版本跟影片差十幾秒時最快） */
  const alignAllToNow = () => {
    const { pos: p, sel: i, lines: ls } = state.current
    if (!ls[i]) return
    edit({ offset: Math.round((p - ls[i].t) * 100) / 100 })
  }

  const deleteLine = (i: number) => {
    if (state.current.lines.length <= 1) return
    edit({ lines: state.current.lines.filter((_, k) => k !== i) })
    setSel(s => Math.min(s, state.current.lines.length - 2))
  }

  const saveText = () => {
    if (!editing) return
    const text = editing.text.trim()
    if (text && text !== lines[editing.i].text) {
      edit({ lines: lines.map((l, k) => (k === editing.i ? { ...l, text } : l)) })
    }
    setEditing(null)
  }

  // ---------------- 自動儲存
  const saveNow = useCallback(async () => {
    const { lines: ls, offset: o } = state.current
    setSaveState('saving')
    try {
      const saved = await api.saveLyrics(slug, {
        lines: ls, offset: o, source: initial.source, lang: initial.lang, lrclib: initial.lrclib ?? null,
      })
      setSaveState(s => (s === 'saving' ? 'saved' : s))
      setSaveErr(null)
      onSaved(saved)
    } catch (e) {
      setSaveState('error')
      setSaveErr((e as Error).message)
    }
  }, [slug, initial, onSaved])

  useEffect(() => {
    if (saveState !== 'dirty') return
    const t = setTimeout(saveNow, 700)
    return () => clearTimeout(t)
  }, [saveState, lines, offset, saveNow])

  // 離開畫面時還沒存的立刻存
  const pending = useRef(false)
  pending.current = saveState === 'dirty'
  useEffect(() => () => { if (pending.current) saveNow() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------- 播放
  const seek = useCallback((t: number) => engine?.seek(Math.max(0, t)), [engine])
  const toggle = useCallback(() => { engine?.toggle() }, [engine])
  const playFrom = (i: number) => {
    if (!engine) return
    engine.seek(Math.max(0, lines[i].t + offset - 1.5))
    if (!engine.playing) engine.play()
  }
  const selectLine = (i: number, seekTo = true) => {
    setSel(i)
    setEditing(null)
    if (seekTo && engine && !engine.playing) engine.seek(Math.max(0, lines[i].t + offset - 1))
  }

  // ---------------- 鍵盤：空白鍵（播放中＝設成現在時間並跳下一句；暫停時＝播放）、↑↓ 選句、←→ 前後 2 秒、Ctrl+Z 復原
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'TEXTAREA' || (tag === 'INPUT' && (e.target as HTMLInputElement).type !== 'range')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); doUndo(); return }
      if (!engine) return
      if (e.code === 'Space') {
        e.preventDefault()
        if (engine.playing) {
          const i = state.current.sel
          stampNow(i)
          setSel(Math.min(i + 1, state.current.lines.length - 1))
        } else engine.play()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault(); setSel(s => Math.min(s + 1, state.current.lines.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); setSel(s => Math.max(s - 1, 0))
      } else if (e.key === 'ArrowRight') {
        e.preventDefault(); engine.seek(engine.position + 2)
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault(); engine.seek(Math.max(0, engine.position - 2))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [engine, stampNow, doUndo])

  // ---------------- 顯示
  const tl = useMemo(() => timed(lines, offset), [lines, offset])
  const current = stageAt(tl, pos).current
  const duration = engine?.duration ?? song.duration ?? 0

  // 播放中讓目前這句保持在清單可見範圍
  useEffect(() => {
    if (!playing || current < 0) return
    listRef.current?.querySelector(`[data-row="${current}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [current, playing])
  useEffect(() => {
    listRef.current?.querySelector(`[data-row="${sel}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [sel])

  // 時間軸：人聲波形 + 每句開始的位置 + 播放位置
  useEffect(() => {
    const cv = canvasRef.current
    if (!cv) return
    const w = cv.clientWidth, h = cv.clientHeight
    const dpr = window.devicePixelRatio || 1
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr) }
    const g = cv.getContext('2d')
    if (!g) return
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, w, h)
    if (!duration) return
    const peaks = engine?.vocalPeaks
    if (peaks) {
      g.fillStyle = '#CFC9BE'
      const bw = w / peaks.length
      for (let k = 0; k < peaks.length; k++) {
        const v = peaks[k] * (h - 6)
        g.fillRect(k * bw, (h - v) / 2, Math.max(1, bw), Math.max(1, v))
      }
    }
    tl.forEach((ln, k) => {
      const x = (ln.t / duration) * w
      g.fillStyle = k === sel ? '#B45309' : '#0F766E'
      g.fillRect(x - (k === sel ? 1 : 0.5), 0, k === sel ? 2 : 1, h)
    })
    g.fillStyle = '#1E1D1A'
    g.fillRect((pos / duration) * w - 1, 0, 2, h)
  }, [tl, sel, pos, duration, engine, stageW])

  const onTimeline = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    seek(((e.clientX - r.left) / r.width) * duration)
  }

  const loadingText = engineErr
    ? engineErr
    : loadP ? `載入歌曲中… ${loadP.stage === 'download' ? `${Math.round(loadP.ratio * 100)}%` : ''}` : '載入歌曲中…'

  return (
    <div className="kt">
      <div className="kt-left" ref={leftRef}>
        <LyricsStage lines={tl} time={pos} width={stageW} />
        <div className="card kt-transport">
          <button className="play" onClick={toggle} disabled={!engine} aria-label={playing ? '暫停' : '播放'}>
            {playing ? <Pause /> : <Play />}
          </button>
          <span className="time">{fmtStamp(pos)}</span>
          <canvas ref={canvasRef} className="kt-timeline" onPointerDown={onTimeline} aria-label="時間軸：點一下跳到那裡" />
          <span className="time muted">{fmtTime(duration)}</span>
        </div>
        {!engine && <div className={`notice ${engineErr ? 'error' : 'warn'}`}>{loadingText}</div>}
        <div className="kt-row2">
          <div className="card kt-offset">
            <div className="slider-head"><b className="kt-h">整體前後移</b><b className="kt-val">{fmtOffset(offset)}</b></div>
            <div className="row">
              <button className="btn sm" onClick={() => edit({ offset: offset - 0.1 })}>−0.1</button>
              <input type="range" min={-OFFSET_MAX} max={OFFSET_MAX} step={0.05} value={offset}
                aria-label="整體前後移"
                onPointerDown={() => { undo.current = [...undo.current.slice(-(UNDO_MAX - 1)), { lines, offset }] }}
                onChange={e => { setOffset(Number(e.target.value)); setSaveState('dirty') }} />
              <button className="btn sm" onClick={() => edit({ offset: offset + 0.1 })}>+0.1</button>
            </div>
            <div className="slider-foot"><span>字幕提早</span>
              <button className="linkbtn" onClick={() => edit({ offset: 0 })} disabled={offset === 0}>歸零</button>
              <span>字幕延後</span></div>
            <button className="btn sm kt-alignall" disabled={!engine} onClick={alignAllToNow}
              title="播放到歌手開始唱「選取的那一句」時按下：整首歌詞一起移動，讓這句剛好對上">
              整首對齊：選取的句子＝現在
            </button>
          </div>
          <div className="card kt-guide">
            <div className="slider-head"><b className="kt-h">原唱音量</b><b className="kt-val">{guide}%</b></div>
            <input type="range" min={0} max={100} step={5} value={guide} aria-label="原唱音量"
              onChange={e => setGuide(Number(e.target.value))} />
            <div className="slider-foot"><span>只聽伴奏</span><span>原唱</span></div>
          </div>
        </div>
        <div className="muted small kt-help">
          其他快捷鍵：<b>↑↓</b> 選句 · <b>←→</b> 前後 2 秒 · <b>Ctrl+Z</b> 復原
        </div>
      </div>

      <div className="card kt-right">
        <div className="kt-listhead">
          <b>單句微調</b>
          <span className="muted small">{lines.length} 句 · {SOURCE_LABEL[initial.source] ?? initial.source}</span>
          <div className="spacer" />
          <button className="btn sm" onClick={doUndo} disabled={!undo.current.length}>復原</button>
        </div>
        <div className="kt-tip">
          <b>怎麼對：</b>點一句選取 → 按播放 → 歌手唱到這句時按<kbd>空白鍵</kbd>，這句就設成現在，並自動跳到下一句，可以跟著歌一句一句點下去。
        </div>
        <div className="kt-list" ref={listRef}>
          {lines.map((ln, i) => {
            const on = i === sel
            return (
              <div key={i} data-row={i} className={`kt-line ${on ? 'on' : ''} ${i === current ? 'now' : ''}`}
                onClick={() => !on && selectLine(i)}>
                <div className="kt-line-main">
                  <span className="kt-stamp">{fmtStamp(ln.t + offset)}</span>
                  {editing?.i === i ? (
                    <input className="kt-edit" value={editing.text} autoFocus aria-label="修改歌詞"
                      onChange={e => setEditing({ i, text: e.target.value })}
                      onKeyDown={e => { if (e.key === 'Enter') saveText(); if (e.key === 'Escape') setEditing(null) }}
                      onBlur={saveText} />
                  ) : (
                    <span className="kt-text">{ln.text}</span>
                  )}
                </div>
                {on && (
                  <div className="kt-tools">
                    <button className="btn sm" onClick={() => nudge(i, -0.1)}>−0.1 秒</button>
                    <button className="btn sm" onClick={() => nudge(i, 0.1)}>+0.1 秒</button>
                    <button className="btn sm primary" onClick={() => stampNow(i)} disabled={!engine}title="把這句的開始時間設成目前播放到的位置（播放中也可以按空白鍵）">設成現在</button>
                    <button className="linkbtn" onClick={() => playFrom(i)} disabled={!engine}>從這句播放</button>
                    <div className="spacer" />
                    <button className="linkbtn" onClick={() => setEditing({ i, text: ln.text })}>改字</button>
                    <button className="linkbtn" onClick={() => deleteLine(i)} disabled={lines.length <= 1}>刪除</button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
        <div className="kt-foot">
          <button className="btn" onClick={() => { if (saveState === 'dirty') saveNow(); onBack() }}>上一步：換歌詞</button>
          <span className={`small kt-save ${saveState === 'error' ? 'kt-err' : 'muted'}`}>
            {saveState === 'saving' ? '儲存中…' : saveState === 'dirty' ? '有修改，稍後自動儲存' : saveState === 'error' ? `儲存失敗：${saveErr}` : '已自動儲存'}
          </span>
          <div className="spacer" />
          <button className="btn primary" disabled={!onNext} onClick={async () => { if (saveState === 'dirty') await saveNow(); onNext?.() }}>下一步：樣式與預覽</button>
        </div>
      </div>
    </div>
  )
}
