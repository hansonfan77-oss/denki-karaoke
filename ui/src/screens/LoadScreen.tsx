/** ① 載入：拖入一首歌，或從「最近處理」直接進預覽；中間檔可以一鍵清除 */
import { useEffect, useRef, useState } from 'react'
import { api, desktop, fmtSize, fmtTime, MODE_LABEL, type Job, type Song, type Status } from '../api'
import { Upload } from '../icons'

const EXTS = '.mp3,.wav,.flac,.m4a,.aac,.ogg,.opus,.wma,.mp4,.mkv,.mov,.m4v,.webm,.avi,.flv'

export default function LoadScreen({ error, status, onJob, onOpen }: {
  error?: string
  status: Status | null
  onJob: (job: Job) => void
  onOpen: (slug: string) => void
}) {
  const [songs, setSongs] = useState<Song[]>([])
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | undefined>(error)
  const [confirm, setConfirm] = useState<string | null>(null)   // 正在確認清除哪一首（'*' = 全部）
  const [notice, setNotice] = useState<string | null>(null)
  const [up, setUp] = useState<{ loaded: number; total: number; since: number; stalled: boolean } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const droppedPath = useRef<string | null>(null)

  const reload = () => api.songs().then(setSongs).catch(() => undefined)
  useEffect(() => { reload() }, [])

  // 桌面視窗：Python 那邊收到拖放事件時會把完整路徑傳進來
  useEffect(() => {
    window.__denkiDroppedPath = (p: string) => { droppedPath.current = p }
    return () => { window.__denkiDroppedPath = undefined }
  }, [])

  // 確認狀態 4 秒沒按就取消
  useEffect(() => {
    if (!confirm) return
    const t = setTimeout(() => setConfirm(null), 4000)
    return () => clearTimeout(t)
  }, [confirm])

  const run = async (label: string, fn: () => Promise<Job>) => {
    setErr(undefined)
    setBusy(label)
    try {
      onJob(await fn())
    } catch (e) {
      setErr((e as Error).message)
      setBusy(null)
      setUp(null)
    }
  }

  // 上傳 10 秒沒有進度 → 提示可能卡住，建議改用「選擇檔案」
  useEffect(() => {
    if (!up || up.stalled) return
    const t = setTimeout(() => setUp(u => (u && u.since === up.since ? { ...u, stalled: true } : u)), 10000)
    return () => clearTimeout(t)
  }, [up])

  const startFromFile = async (file: File) => {
    // 先等一下 Python 端送完整路徑（桌面版）；拿不到就上傳（瀏覽器版）
    const direct = (file as File & { pywebviewFullPath?: string }).pywebviewFullPath
    if (!direct && desktop()) await new Promise(r => setTimeout(r, 350))
    const path = direct || droppedPath.current
    droppedPath.current = null
    if (path) return run('讀取中…', () => api.analyze(path))
    setUp({ loaded: 0, total: file.size, since: Date.now(), stalled: false })
    return run(`上傳中… ${file.name}`, () => api.upload(file, undefined, (loaded, total) =>
      setUp(u => (u && loaded === u.loaded ? u : { loaded, total, since: Date.now(), stalled: false }))))
  }

  const pick = async () => {
    const d = desktop()
    if (d) {
      const p = await d.pick_file()
      if (p) run('讀取中…', () => api.analyze(p))
    } else {
      input.current?.click()
    }
  }

  const openRecent = (s: Song) => {
    if (s.analyzed) return onOpen(s.slug)
    if (s.sourceExists) return run('讀取中…', () => api.analyze(s.source))
    setErr(`找不到原始檔案：${s.source}`)
  }

  const clearOne = async (s: Song) => {
    if (confirm !== s.slug) return setConfirm(s.slug)
    setConfirm(null)
    const r = await api.clearSong(s.slug)
    setNotice(`已清除「${s.title}」的中間檔，釋放 ${fmtSize(r.freed)}。輸出的成品都還在。`)
    reload()
  }

  const clearAll = async () => {
    if (confirm !== '*') return setConfirm('*')
    setConfirm(null)
    const r = await api.clearAll()
    setNotice(`已清除全部中間檔，釋放 ${fmtSize(r.freed)}。輸出的成品都還在。`)
    reload()
  }

  const total = songs.reduce((a, s) => a + s.cacheBytes, 0)
  const defaultLabel = status ? MODE_LABEL[status.defaultMode] : null

  return (
    <main className="main center" style={{ gap: 24 }}>
      <div
        className={`drop ${over ? 'over' : ''}`}
        onDragOver={e => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={e => {
          e.preventDefault()
          setOver(false)
          const f = e.dataTransfer.files[0]
          if (f) startFromFile(f)
        }}
      >
        <Upload />
        <h2 style={{ maxWidth: 640, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{busy ?? '拖入一首歌'}</h2>
        {up && busy && (
          <div className="upload-prog">
            <div className="bar"><div style={{ width: `${up.total ? Math.round((up.loaded / up.total) * 100) : 0}%` }} /></div>
            <div className="muted small">
              {up.loaded >= up.total
                ? '上傳完成，準備分析…'
                : `已上傳 ${fmtSize(up.loaded)} / ${fmtSize(up.total)}（${up.total ? Math.floor((up.loaded / up.total) * 100) : 0}%）`}
            </div>
            {up.stalled && up.loaded < up.total && (
              <div className="notice warn" style={{ marginTop: 4 }}>
                10 秒沒有進度，可能卡住了。可以重新開啟程式，改按「選擇檔案」挑同一首歌：不用上傳，直接讀取，大檔案比較快。
              </div>
            )}
          </div>
        )}
        <div className="muted small">
          音檔 mp3 / wav / flac / m4a，或影片 mp4 / mkv / mov
          {defaultLabel && ` · 預設用「${defaultLabel}」模式分析，之後可以切換`}
          <br />大檔案建議按「選擇檔案」：不用上傳，直接讀取，比較快
        </div>
        <button className="btn" style={{ marginTop: 6 }} onClick={pick} disabled={!!busy}>選擇檔案</button>
        <input ref={input} type="file" accept={EXTS} hidden
          onChange={e => { const f = e.target.files?.[0]; if (f) startFromFile(f); e.target.value = '' }} />
      </div>

      {err && <div className="notice error" style={{ width: 640 }}>{err}</div>}
      {notice && <div className="notice warn" style={{ width: 640 }}>{notice}</div>}

      {songs.length > 0 && (
        <div className="recent">
          <div className="recent-head">
            <div className="label">最近處理</div>
            <div className="spacer" />
            {total > 0 && (
              <>
                <span className="muted small">中間檔共 {fmtSize(total)}</span>
                <button className="linkbtn" onClick={clearAll} style={confirm === '*' ? { color: 'var(--error-text)' } : undefined}>
                  {confirm === '*' ? '確定全部清除？再按一次' : '全部清除'}
                </button>
              </>
            )}
          </div>
          <div className="card recent-list">
            {songs.slice(0, 8).map(s => (
              <div key={s.slug} className="recent-item">
                <button className="recent-row" onClick={() => openRecent(s)} disabled={!!busy}>
                  <span className="t">{s.title}</span>
                  <span className="muted small" style={{ whiteSpace: 'nowrap' }}>
                    {s.hasVideo ? '影片' : '音檔'} · {fmtTime(s.duration)} ·
                    {s.analyzedModes.length
                      ? s.analyzedModes.map(m => <span key={m} className="tag">{MODE_LABEL[m]}</span>)
                      : ' 尚未分析'}
                  </span>
                </button>
                {s.cacheBytes > 0 && (
                  <button className={`trash ${confirm === s.slug ? 'confirm' : ''}`} onClick={() => clearOne(s)}
                    title="刪除這首歌的中間檔（輸出的成品不會刪）" aria-label={`清除 ${s.title} 的中間檔`}>
                    {confirm === s.slug ? '確定清除？' : `清除 ${fmtSize(s.cacheBytes)}`}
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="muted small">
            分析過的歌會保留中間檔，之後改 Key、改導唱、換模式都不用再等 AI。不用的歌可以清除，要用時重新拖進來即可。
          </div>
        </div>
      )}
    </main>
  )
}
