/** 程式內更新（第 3 批）：頂部的「有新版本」按鈕、更新對話框、底部版本列、更新完成的提示。 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtSize, type UpdateInfo } from './api'
import { Check } from './icons'

const BUSY = ['downloading', 'installing', 'restarting'] as const

export function useUpdate() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const alive = useRef(true)

  // 開啟時後端已經在背景問 GitHub；這裡稍等一下再拿結果，之後每 30 分鐘再問一次
  useEffect(() => {
    alive.current = true
    const get = () => api.update().then(i => alive.current && setInfo(i)).catch(() => {})
    get()
    const t1 = setTimeout(get, 3000)
    const t2 = setTimeout(get, 10000)
    const t3 = setInterval(() => api.update(true).then(i => alive.current && setInfo(i)).catch(() => {}), 30 * 60 * 1000)
    return () => { alive.current = false; clearTimeout(t1); clearTimeout(t2); clearInterval(t3) }
  }, [])

  const refresh = useCallback(async () => {
    setChecking(true)
    try { setInfo(await api.update(true)) } catch { /* 後端沒回應就算了 */ }
    setChecking(false)
  }, [])

  return { info, setInfo, checking, refresh }
}

export function UpdatePill({ info, onClick }: { info: UpdateInfo | null; onClick: () => void }) {
  if (!info?.available || !info.latest || !info.enabled) return null
  const busy = (BUSY as readonly string[]).includes(info.state)
  return (
    <button className="pill update-pill" onClick={onClick} title="點一下看這次改了什麼">
      <span className="dot" />
      <span>{busy ? `正在更新到 ${info.latest.version}…` : `有新版本 ${info.latest.version} · 更新`}</span>
    </button>
  )
}

function ago(t: number | null): string {
  if (!t) return '還沒檢查'
  const s = Date.now() / 1000 - t
  if (s < 90) return '剛剛'
  if (s < 3600) return `${Math.round(s / 60)} 分鐘前`
  return `${Math.round(s / 3600)} 小時前`
}

export function UpdateFooter({ info, checking, onCheck, onOpen }: {
  info: UpdateInfo | null; checking: boolean; onCheck: () => void; onOpen: () => void
}) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(n => n + 1), 30000); return () => clearInterval(t) }, [])
  if (!info) return null
  let status: React.ReactNode
  if (!info.enabled) status = <span title={info.disabledReason}>開發版（不自動更新）</span>
  else if (checking || info.state === 'checking') status = '檢查中…'
  else if (info.available && info.latest) status = <button className="linkbtn" onClick={onOpen}>有新版本 {info.latest.version}</button>
  else if (info.error) status = <>上次檢查：{info.error}</>
  else status = <>上次檢查：{ago(info.checkedAt)}{info.checkedAt ? ' · 已是最新版' : ''}</>
  return (
    <footer className="footbar">
      <span>DENKI 伴奏工具 v{info.current}</span>
      <span>·</span>
      <span>{status}</span>
      {info.enabled && !checking && info.state !== 'checking' && (
        <button className="linkbtn" onClick={onCheck}>檢查更新</button>
      )}
    </footer>
  )
}

/** 條列 GitHub 發佈說明（Markdown 的「- 項目」當成清單，其餘當一般文字） */
function Notes({ text }: { text: string }) {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  if (!lines.length) return <p className="muted">（這一版沒有寫說明）</p>
  const items: React.ReactNode[] = []
  let list: string[] = []
  const flush = () => {
    if (list.length) items.push(<ul key={items.length}>{list.map((l, i) => <li key={i}>{l}</li>)}</ul>)
    list = []
  }
  for (const l of lines) {
    const m = l.match(/^[-*•]\s+(.*)$/)
    if (m) list.push(m[1].replace(/\*\*/g, ''))
    else { flush(); items.push(<p key={items.length}>{l.replace(/^#+\s*/, '').replace(/\*\*/g, '')}</p>) }
  }
  flush()
  return <>{items}</>
}

export function UpdateDialog({ info, setInfo, onClose }: {
  info: UpdateInfo; setInfo: (i: UpdateInfo) => void; onClose: () => void
}) {
  const [err, setErr] = useState<string | null>(null)
  const [gone, setGone] = useState(false)   // 後端已經關掉（小幫手接手了）
  const busy = (BUSY as readonly string[]).includes(info.state)

  useEffect(() => {
    if (!busy) return
    let stop = false
    const poll = async () => {
      while (!stop) {
        try { setInfo(await api.update()) } catch { setGone(true) }
        await new Promise(r => setTimeout(r, 400))
      }
    }
    poll()
    return () => { stop = true }
  }, [busy, setInfo])

  const start = async () => {
    setErr(null)
    try { setInfo(await api.startUpdate()) } catch (e) { setErr((e as Error).message) }
  }

  const latest = info.latest
  if (!latest) return null

  if (!busy && info.state !== 'error') {
    return (
      <div className="modal-back" onClick={onClose}>
        <div className="modal" role="dialog" aria-label="有新版本可以更新" onClick={e => e.stopPropagation()}>
          <h2>有新版本可以更新</h2>
          <div className="muted">
            目前 v{info.current} → 新版 <b className="accent">{latest.version}</b>
            {latest.published && `（${latest.published} 發佈）`}
          </div>
          <div className="notes"><Notes text={latest.notes} /></div>
          <div className="small muted">
            約 1 分鐘，會自動重新開啟。已處理過的歌、輸出的檔案、下載過的 AI 模型都會保留。
          </div>
          {err && <div className="notice error">{err}</div>}
          <div className="modal-btns">
            <button className="btn" onClick={onClose}>之後再說</button>
            <button className="btn primary" onClick={start}>立刻更新</button>
          </div>
        </div>
      </div>
    )
  }

  // 進度
  const st = info.state
  const dlDone = st !== 'downloading'
  const depsText = info.depsTodo == null ? ''
    : info.torchChange ? '（這次包含 AI 核心 PyTorch，約 2.5 GB，會開一個小視窗顯示進度）'
      : info.depsTodo.length ? `（${info.depsTodo.length} 個套件要更新，會開一個小視窗顯示進度）`
        : '（這次沒有新套件，幾秒就好）'
  const pct = st === 'downloading'
    ? (info.total ? 5 + 55 * Math.min(1, info.bytes / info.total) : 30)
    : st === 'installing' ? 70 : st === 'restarting' ? 92 : 100
  const mark = (done: boolean, now: boolean) =>
    <span className={`mark ${done ? 'done' : now ? 'now' : ''}`}>{done && <Check size={14} />}</span>

  return (
    <div className="modal-back">
      <div className="modal" role="dialog" aria-label={`正在更新到 ${latest.version}`}>
        {st === 'error' ? (
          <>
            <h2>更新沒有完成</h2>
            <div className="notice error">{info.error}</div>
            <div className="small muted">目前的版本沒有任何變動，可以照常使用。之後再按「檢查更新」重試。</div>
            <div className="modal-btns"><button className="btn primary" onClick={onClose}>知道了</button></div>
          </>
        ) : (
          <>
            <h2>正在更新到 {latest.version}</h2>
            <div className="ustep">
              {mark(dlDone, !dlDone)}
              <span>下載新版程式{info.bytes ? `（${fmtSize(info.bytes)}）` : '…'}</span>
            </div>
            <div className="ustep">
              {mark(false, st === 'installing')}
              <span>補裝新套件{st === 'installing' ? '…' : ''}</span>
              <span className="small muted">{depsText}</span>
            </div>
            <div className="ustep">
              {mark(false, st === 'restarting')}
              <span>重新開啟伴奏工具</span>
              {(st === 'restarting' || gone) && <span className="small muted">（這個視窗會關掉，稍等一下會自動開新的）</span>}
            </div>
            <div className="bar"><div style={{ width: `${pct}%` }} /></div>
            <div className="small muted">更新失敗會自動退回舊版，不會壞掉。</div>
          </>
        )}
      </div>
    </div>
  )
}

export function UpdateResult({ info, onAck }: { info: UpdateInfo | null; onAck: () => void }) {
  const r = info?.lastResult
  if (!r) return null
  return (
    <div className={`update-result ${r.ok ? 'ok' : 'bad'}`} role="status">
      <span>{r.ok ? `✓ ${r.message}` : r.message}</span>
      <button className="btn sm" onClick={onAck}>知道了</button>
    </div>
  )
}
