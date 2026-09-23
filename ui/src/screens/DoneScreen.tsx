/** ④ 完成 */
import { api, fmtSize, type RenderResult } from '../api'
import { Check, Film, Folder, Note, Play } from '../icons'

export default function DoneScreen({ result, seconds, onAgain, onNext }: {
  result: RenderResult
  seconds: number
  onAgain: () => void
  onNext: () => void
}) {
  return (
    <main className="main center" style={{ gap: 24 }}>
      <span className="ok-badge"><Check size={28} /></span>
      <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 4 }}>
        <h1>輸出完成 · 用時 {Math.max(1, Math.round(seconds))} 秒</h1>
        <div className="muted small" style={{ userSelect: 'text' }}>{result.outDir}</div>
      </div>

      <div className="card files">
        {result.files.map(f => (
          <div key={f.path} className="file">
            {f.kind === 'video' ? <Film /> : <Note />}
            <div className="t">
              <span title={f.name}>{f.name}</span>
              <span className="muted small">{f.kind === 'video' ? '影片' : f.name.split('.').pop()?.toUpperCase()} · {fmtSize(f.size)}</span>
            </div>
            <button className="icon-btn" aria-label={`播放 ${f.name}`} onClick={() => api.open(f.path)}>
              <Play size={16} color="#1E1D1A" />
            </button>
          </div>
        ))}
      </div>

      <div className="row">
        {result.outDir && <button className="btn" onClick={() => api.open(result.outDir!)}><Folder />開啟資料夾</button>}
        <button className="btn" onClick={onAgain}>回去再調一版</button>
        <button className="btn primary" onClick={onNext}>處理下一首</button>
      </div>
      <button className="btn sm ghost" disabled title="第二期">用這首做卡拉影片（第二期）</button>
    </main>
  )
}
