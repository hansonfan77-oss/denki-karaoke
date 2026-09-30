/** ① 歌詞：LRCLIB 搜尋／貼上純文字（AI 對時間，或自己點）／匯入 LRC 檔 */
import { useEffect, useRef, useState } from 'react'
import {
  api, fmtSize, fmtTime,
  type AlignJob, type AlignStatus, type LrclibResult, type LyricLine, type Lyrics, type Song,
} from '../api'
import { detectLang, fmtStamp, japaneseOnly, LANG_LABEL, SOURCE_LABEL, splitPlain, spreadEvenly } from './lyricsView'

type Tab = 'lrclib' | 'paste' | 'lrc'

export default function LyricsStep({ song, existing, titleGuess, onUse, onContinue }: {
  song: Song
  existing: Lyrics | null
  titleGuess: string
  onUse: (l: Lyrics) => void        // 存好新的歌詞 → 進入對時間
  onContinue: () => void            // 沿用已經有的歌詞
}) {
  const [tab, setTab] = useState<Tab>('lrclib')
  const [pasteText, setPasteText] = useState('')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { setErr(null) }, [tab])

  const save = async (lines: LyricLine[], source: Lyrics['source'], extra: Partial<Lyrics> = {}) => {
    setErr(null)
    try {
      const l = await api.saveLyrics(song.slug, { lines, offset: 0, source, lang: extra.lang, lrclib: extra.lrclib ?? null })
      onUse(l)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  return (
    <div className="card ks-card">
      {existing && existing.lines.length > 0 && (
        <div className="ks-existing">
          <div>
            <b>這首歌已經有歌詞</b>
            <span className="muted small">　{existing.lines.length} 句 · {SOURCE_LABEL[existing.source] ?? existing.source}
              {existing.updated_at ? ` · ${existing.updated_at.replace('T', ' ').slice(0, 16)}` : ''}</span>
          </div>
          <div className="spacer" />
          <span className="muted small">換一份會取代目前的歌詞</span>
          <button className="btn primary" onClick={onContinue}>繼續對時間</button>
        </div>
      )}
      <div className="ks-src-head">歌詞從哪裡來？選一種：</div>
      <div className="ks-tabs" role="tablist">
        {([
          ['lrclib', '搜尋歌詞', '線上找附時間的歌詞（LRCLIB）'],
          ['paste', '貼上歌詞', 'AI 聽人聲自動對時間'],
          ['lrc', '匯入 LRC 檔', '已經有附時間的歌詞檔'],
        ] as [Tab, string, string][]).map(([id, label, sub]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`ks-tab ${tab === id ? 'on' : ''}`}
            onClick={() => setTab(id)}>
            <b>{label}</b><span>{sub}</span>
          </button>
        ))}
      </div>
      <div className="ks-body">
        {/* 三個分頁都保持掛著（切換分頁時 AI 對時間的進度不會中斷） */}
        <div hidden={tab !== 'lrclib'}>
          <LrclibTab song={song} titleGuess={titleGuess}
            onUseSynced={r => save(r.lines, 'lrclib', { lrclib: { id: r.id, trackName: r.trackName, artistName: r.artistName } })}
            onUsePlain={text => { setPasteText(text); setTab('paste') }} />
        </div>
        <div hidden={tab !== 'paste'}>
          <PasteTab song={song} text={pasteText} setText={setPasteText} onAligned={onUse}
            onManual={texts => save(spreadEvenly(texts, song.duration ?? 240), 'manual')} />
        </div>
        <div hidden={tab !== 'lrc'}>
          <LrcTab onUse={(lines, lang) => save(lines, 'lrc', { lang })} />
        </div>
      </div>
      {err && <div className="notice error" style={{ margin: '0 20px 16px' }}>{err}</div>}
    </div>
  )
}

// ------------------------------------------------------------------ LRCLIB
function LrclibTab({ song, titleGuess, onUseSynced, onUsePlain }: {
  song: Song
  titleGuess: string
  onUseSynced: (r: LrclibResult) => void
  onUsePlain: (text: string) => void
}) {
  const [title, setTitle] = useState(titleGuess)
  const [artist, setArtist] = useState('')
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<LrclibResult[] | null>(null)
  const [pick, setPick] = useState(0)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { setTitle(titleGuess); setResults(null) }, [titleGuess])

  const search = async () => {
    if (!title.trim()) return
    setBusy(true)
    setErr(null)
    try {
      const r = await api.searchLyrics(title.trim(), artist.trim(), song.duration)
      setResults(r.results)
      setPick(0)
    } catch (e) {
      setErr((e as Error).message)
      setResults(null)
    } finally {
      setBusy(false)
    }
  }

  const sel = results?.[pick]
  return (
    <div className="ks-split">
      <div className="ks-col">
        <form className="ks-search" onSubmit={e => { e.preventDefault(); search() }}>
          <label className="ks-field">歌名
            <input value={title} onChange={e => setTitle(e.target.value)} placeholder="歌名" />
          </label>
          <label className="ks-field">歌手（可空白）
            <input value={artist} onChange={e => setArtist(e.target.value)} />
          </label>
          <button className="btn primary" type="submit" disabled={busy || !title.trim()}>{busy ? '搜尋中…' : '搜尋'}</button>
        </form>
        {err && <div className="notice error">{err}</div>}
        {results && (
          <div className="muted small">
            {results.length ? `找到 ${results.length} 筆 · 已依「附時間」與「時長接近」排序` : '找不到。可以改短一點的歌名、加上歌手，或改用「貼上歌詞」。'}
          </div>
        )}
        <div className="ks-results">
          {results?.map((r, i) => (
            <button key={r.id} className={`ks-result ${i === pick ? 'on' : ''}`} onClick={() => setPick(i)}>
              <span className="ks-rt">
                <span className="ks-rname">{r.trackName}</span>
                <span className="muted small">{r.artistName || '（未標示歌手）'} · {fmtTime(r.duration)}{r.albumName ? ` · ${r.albumName}` : ''}</span>
              </span>
              <span className={`ks-badge ${r.synced ? 'ok' : 'warn'}`}>{r.synced ? '附時間' : '只有文字'}</span>
              {r.durationDiff != null && (
                <span className={`ks-badge ${r.durationDiff <= 2 ? 'ok' : ''}`}>{r.durationDiff <= 2 ? '時長相符' : `差 ${Math.round(r.durationDiff)} 秒`}</span>
              )}
            </button>
          ))}
        </div>
        {!results && !err && (
          <div className="ks-tip">歌名已經從檔名猜好了，可以直接按「搜尋」。找不到或只有純文字時，改用「貼上歌詞」讓 AI 對時間。</div>
        )}
      </div>
      <div className="ks-col ks-preview">
        {sel ? (
          <>
            <div className="muted small">預覽（第 {pick + 1} 筆）</div>
            <div className="ks-lines">
              {sel.synced
                ? sel.lines.slice(0, 60).map((l, i) => (
                  <div key={i}><span className="ks-ts">{fmtStamp(l.t)}</span>{l.text}</div>))
                : sel.plain.split('\n').slice(0, 60).map((l, i) => <div key={i}>{l || ' '}</div>)}
              {(sel.synced ? sel.lines.length : sel.plain.split('\n').length) > 60 && <div className="muted">…</div>}
            </div>
            <div className="ks-actions">
              {sel.synced && sel.durationDiff != null && sel.durationDiff > 2 && (
                <span className="muted small">時長差 {Math.round(sel.durationDiff)} 秒，下一步可以用「整體前後移」對齊</span>
              )}
              <div className="spacer" />
              {sel.synced
                ? <button className="btn primary" onClick={() => onUseSynced(sel)}>用這份歌詞 → 對時間</button>
                : <button className="btn primary" onClick={() => onUsePlain(sel.plain)}>用這份文字 → AI 對時間</button>}
            </div>
          </>
        ) : (
          <div className="ks-empty muted">搜尋結果的歌詞會顯示在這裡</div>
        )}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ 貼上歌詞
function PasteTab({ song, text, setText, onAligned, onManual }: {
  song: Song
  text: string
  setText: (t: string) => void
  onAligned: (l: Lyrics) => void
  onManual: (texts: string[]) => void
}) {
  const [st, setSt] = useState<AlignStatus | null>(null)
  const [job, setJob] = useState<AlignJob | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  useEffect(() => { api.alignStatus().then(setSt).catch(() => undefined) }, [])

  const [jaOnly, setJaOnly] = useState(true)
  const all = splitPlain(text)
  const filtered = japaneseOnly(all)
  const lines = jaOnly ? filtered.keep : all
  const lang = detectLang(lines.join('\n'))
  const running = job?.state === 'running'

  const start = async () => {
    setErr(null)
    try {
      let j = await api.align(song.slug, lines.join('\n'), lang)
      setJob(j)
      while (j.state === 'running') {
        await new Promise(r => setTimeout(r, 500))
        if (!alive.current) return
        try { j = await api.alignJob(j.id) } catch { continue }
        setJob(j)
      }
      if (j.state === 'done' && j.lyrics) onAligned(j.lyrics)
      else if (j.state === 'error') setErr(j.error)
      api.alignStatus().then(s => alive.current && setSt(s)).catch(() => undefined)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const cancel = () => { if (job) api.cancelAlign(job.id).catch(() => undefined) }

  return (
    <div className="ks-split">
      <div className="ks-col">
        <label className="ks-field">把歌詞貼在這裡（一行一句；空行、[副歌] 之類的標記會自動略過）
          <textarea className="ks-textarea" value={text} onChange={e => setText(e.target.value)} disabled={running}
            placeholder={'第一句歌詞\n第二句歌詞\n…'} />
        </label>
        <div className="muted small">{lines.length} 句{lines.length ? ` · 語言判斷：${LANG_LABEL[lang]}` : ''}</div>
        {filtered.dropped > 0 && (
          <label className="check ks-filter">
            <input type="checkbox" checked={jaOnly} onChange={e => setJaOnly(e.target.checked)} disabled={running} />
            只保留日文：送去對時間時略過 {filtered.dropped} 行中文翻譯、作詞作曲資訊（{all.length} 行 → {filtered.keep.length} 句）
          </label>
        )}
        {filtered.dropped > 0 && jaOnly && (
          <button className="btn sm" style={{ alignSelf: 'flex-start' }} disabled={running}
            onClick={() => setText(filtered.keep.join('\n'))}>把略過的行從上面的文字框刪掉（看得到結果）</button>
        )}
      </div>
      <div className="ks-col">
        <div className="ks-box">
          <b>AI 自動對時間</b>
          <div className="small ks-p">用這首歌分析時留下的「人聲」來聽，比用原曲準。對完一樣可以在下一步微調。
            {st && (st.device === 'cuda' ? ' 顯卡約 30 秒。' : ' 這台沒有顯卡，約需 3～8 分鐘。')}</div>
        </div>
        {st && !st.packages && (
          <div className="notice error">這台電腦還沒安裝 AI 對時間元件。請先更新到最新版（右上角），或執行「更新並測試.bat」。</div>
        )}
        {st && st.packages && !st.model && !running && (
          <div className="notice warn">第一次使用要下載對時間模型（約 {fmtSize(st.modelBytes)}），只下載一次。只用伴奏處理的電腦不會下載。</div>
        )}
        {job && running && (
          <div className="ks-box">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span>{job.message}</span>
              <span className="muted small">{Math.round(job.elapsed)} 秒</span>
            </div>
            <div className={`bar ${job.stage === 'align' && job.progress < 1 ? 'indet' : ''}`}>
              <div style={{ width: `${job.stage === 'load' || job.stage === 'prepare' ? 3 : Math.max(3, job.progress)}%` }} />
            </div>
          </div>
        )}
        {job?.state === 'cancelled' && <div className="notice warn">已取消。</div>}
        {err && <div className="notice error">{err}</div>}
        <div className="spacer" />
        <div className="ks-actions">
          <button className="linkbtn" disabled={!lines.length || running} onClick={() => onManual(lines)}
            title="不用 AI：句子先平均排好，下一步跟著歌按空白鍵一句一句點">不用 AI，自己點時間</button>
          <div className="spacer" />
          {running
            ? <button className="btn" onClick={cancel}>取消</button>
            : <button className="btn primary" disabled={!lines.length || (st != null && !st.packages)} onClick={start}>開始對時間</button>}
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ 匯入 LRC
function LrcTab({ onUse }: { onUse: (lines: LyricLine[], lang: string) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [parsed, setParsed] = useState<{ name: string; lines: LyricLine[]; lang: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const read = async (f: File) => {
    setErr(null)
    setParsed(null)
    try {
      const buf = await f.arrayBuffer()
      let text = new TextDecoder('utf-8').decode(buf)
      if (text.includes('�')) {
        try { text = new TextDecoder('big5').decode(buf) } catch { /* 沒有這個編碼就維持 UTF-8 */ }
      }
      const r = await api.parseLrc(text)
      setParsed({ name: f.name, ...r })
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  return (
    <div className="ks-split">
      <div className="ks-col">
        <div className="ks-drop"
          onDragOver={e => e.preventDefault()}
          onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) read(f) }}>
          <b>把 .lrc 檔拖到這裡</b>
          <span className="muted small">或</span>
          <button className="btn" onClick={() => input.current?.click()}>選擇 LRC 檔</button>
          <input ref={input} type="file" accept=".lrc,.txt" hidden
            onChange={e => { const f = e.target.files?.[0]; if (f) read(f); e.target.value = '' }} />
          <span className="muted small">LRC 是附時間的歌詞檔，每行像 [00:12.34]歌詞。之前輸出時勾「同時輸出 .lrc」的檔案也可以匯入。</span>
        </div>
        {err && <div className="notice error">{err}</div>}
      </div>
      <div className="ks-col ks-preview">
        {parsed ? (
          <>
            <div className="muted small">{parsed.name} · {parsed.lines.length} 句 · {LANG_LABEL[parsed.lang] ?? parsed.lang}</div>
            <div className="ks-lines">
              {parsed.lines.slice(0, 60).map((l, i) => <div key={i}><span className="ks-ts">{fmtStamp(l.t)}</span>{l.text}</div>)}
            </div>
            <div className="ks-actions"><div className="spacer" />
              <button className="btn primary" onClick={() => onUse(parsed.lines, parsed.lang)}>用這份歌詞 → 對時間</button>
            </div>
          </>
        ) : (
          <div className="ks-empty muted">讀到的歌詞會顯示在這裡</div>
        )}
      </div>
    </div>
  )
}
