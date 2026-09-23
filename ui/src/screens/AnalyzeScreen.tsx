/** ② 分析中：每首歌只跑一次 */
import { useEffect, useState } from 'react'
import { api, fmtTime, type Job, type Status } from '../api'
import { Check } from '../icons'

const STEPS: { stage: Job['stage']; name: string }[] = [
  { stage: 'read', name: '讀取檔案、抽出音軌' },
  { stage: 'separate', name: 'AI 分離人聲與伴奏' },
  { stage: 'save', name: '保存中間檔（純伴奏、純人聲）' },
]
const ORDER: Job['stage'][] = ['read', 'separate', 'save']

export default function AnalyzeScreen({ job: initial, status, onDone, onBack }: {
  job: Job
  status: Status | null
  onDone: (slug: string) => void
  onBack: (error?: string) => void
}) {
  const [job, setJob] = useState(initial)

  useEffect(() => {
    let stop = false
    const tick = async () => {
      try {
        const j = await api.job(initial.id)
        if (stop) return
        setJob(j)
        if (j.state === 'done' && j.slug) return onDone(j.slug)
        if (j.state === 'cancelled') return onBack()
        if (j.state === 'error') return
      } catch { /* 暫時連不上就下次再試 */ }
      if (!stop) setTimeout(tick, 500)
    }
    tick()
    return () => { stop = true }
  }, [initial.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const now = ORDER.indexOf(job.stage)
  const name = job.source.split(/[\\/]/).pop()
  const cpu = status ? status.device !== 'cuda' : false
  const downloading = job.message.includes('下載')

  return (
    <main className="main center" style={{ gap: 28 }}>
      <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <h1>正在分析：{name}</h1>
        <div className="small" style={{ color: 'var(--accent-dark)', fontWeight: 500 }}>「{job.modeLabel}」模式</div>
        <div className="muted small">
          這一步每首歌只做一次 · 已用 {fmtTime(job.elapsed)}
          {job.eta != null && ` · 預估剩餘 約 ${fmtTime(job.eta)}`}
        </div>
      </div>

      <div className="card steps">
        {STEPS.map((s, i) => {
          const state = job.state === 'done' || i < now ? 'done' : i === now ? 'now' : 'todo'
          return (
            <div key={s.stage} className={`step ${state}`} style={{ flexWrap: 'wrap' }}>
              <span className={`mark ${state}`}>{state === 'done' && <Check />}</span>
              <span className="name">{s.stage === 'separate' && state === 'now' ? job.message : s.name}</span>
              {s.stage === 'separate' && state === 'now' && (
                <span className="small" style={{ fontWeight: 500, color: 'var(--accent-dark)' }}>{Math.floor(job.progress)}%</span>
              )}
              {state === 'todo' && <span className="muted small">等待中</span>}
              {s.stage === 'separate' && state === 'now' && (
                <div className="bar" style={{ flexBasis: '100%', marginLeft: 42, marginTop: 10 }}>
                  <div style={{ width: `${job.progress}%` }} />
                </div>
              )}
            </div>
          )
        })}
      </div>

      {job.state === 'error' ? (
        <>
          <div className="notice error" style={{ width: 560 }}>分析失敗：{job.error}</div>
          <button className="btn" onClick={() => onBack()}>返回</button>
        </>
      ) : (
        <>
          {downloading && (
            <div className="notice warn" style={{ width: 560 }}>
              第一次使用「{job.modeLabel}」模式，要先下載 AI 模型（高品質約 600 MB、保留和聲約 870 MB），只有這一次。
            </div>
          )}
          {cpu && (
            <div className="notice warn" style={{ width: 560 }}>
              這台電腦沒有可用的 NVIDIA 顯卡，改用 CPU 模式，約 3～8 分鐘；功能完全相同。
              分析完成後的預覽和調整都是即時的，跟顯卡無關。
            </div>
          )}
          <button className="btn" onClick={() => api.cancel(job.id)}>取消</button>
        </>
      )}
    </main>
  )
}
