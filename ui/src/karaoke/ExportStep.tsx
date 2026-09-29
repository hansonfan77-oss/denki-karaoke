/** ④ 輸出：伴奏設定（模式、Key、導唱）、存放位置、同時輸出 .lrc → 1080p MP4（字幕燒進畫面） */
import { useEffect, useRef, useState } from 'react'
import { api, desktop, keyLabel, MODE_LABEL, type KaraokeJob, type KaraokeSettings, type Lyrics, type ModeId, type Song } from '../api'
import { Check, Folder } from '../icons'
import { fmtOffset } from './lyricsView'

const BG_LABEL: Record<string, string> = { video: '原影片', color: '單色', image: '自選圖片', cover: '模糊封面' }
const KEY_MIN = -12, KEY_MAX = 12

export default function ExportStep({ song, lyrics, settings, onSettings, onBack }: {
  song: Song
  lyrics: Lyrics
  settings: KaraokeSettings
  onSettings: (s: KaraokeSettings) => void
  onBack: () => void
}) {
  const slug = song.slug
  const [audio, setAudio] = useState(settings.audio)
  const [dirty, setDirty] = useState(false)
  const [job, setJob] = useState<KaraokeJob | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const setA = (patch: Partial<typeof audio>) => { setAudio(a => ({ ...a, ...patch })); setDirty(true) }
  useEffect(() => {
    if (!dirty) return
    const t = setTimeout(async () => {
      try { onSettings(await api.saveKaraoke(slug, { audio })); setDirty(false) } catch (e) { setErr((e as Error).message) }
    }, 400)
    return () => clearTimeout(t)
  }, [dirty, audio, slug, onSettings])

  const pickDir = async () => {
    const d = desktop()
    if (!d) return
    const p = await d.pick_folder()
    if (p) {
      await api.setOutDir(p)
      onSettings(await api.karaoke(slug))
    }
  }

  const start = async () => {
    setErr(null)
    try {
      if (dirty) { onSettings(await api.saveKaraoke(slug, { audio })); setDirty(false) }
      let j = await api.renderKaraoke(slug)
      setJob(j)
      while (j.state === 'running') {
        await new Promise(r => setTimeout(r, 500))
        if (!alive.current) return
        try { j = await api.karaokeJob(j.id) } catch { continue }
        setJob(j)
      }
      if (j.state === 'error') setErr(j.error)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const running = job?.state === 'running'
  const done = job?.state === 'done' && job.result
  const s = settings.style
  const font = settings.fonts.find(f => f.id === s.font)?.label ?? s.font

  return (
    <div className="kx">
      <div className="card kx-card">
        <h2 className="kx-title">輸出卡拉影片</h2>
        <div className="kx-sum">
          <div><span>畫質</span>1080p MP4（字幕燒進畫面）</div>
          <div><span>歌詞</span>{lyrics.lines.length} 句 · 整體 {fmtOffset(lyrics.offset)}</div>
          <div><span>字幕</span>{font} · {s.size} · {s.plate === 'none' ? '無底板' : `${s.plate === 'black' ? '黑' : '白'}色底板 ${s.plateOpacity}%`}</div>
          <div><span>背景</span>{BG_LABEL[settings.resolvedBg.kind]}</div>
        </div>

        <div className="kx-audio">
          <div className="ksty-h">伴奏（預設沿用「伴奏處理」最後一次輸出的設定）</div>
          <div className="kx-row">
            <span className="kx-lab">分離模式</span>
            <div className="seg">
              {settings.analyzedModes.map((m: ModeId) => (
                <button key={m} className={audio.mode === m ? 'on' : ''} disabled={running} onClick={() => setA({ mode: m })}>{MODE_LABEL[m]}</button>
              ))}
            </div>
          </div>
          <div className="kx-row">
            <span className="kx-lab">Key</span>
            <button className="icon-btn" disabled={running || audio.key <= KEY_MIN} onClick={() => setA({ key: audio.key - 1 })} aria-label="降一個 Key">−</button>
            <b className="kx-key">{keyLabel(audio.key)}</b>
            <button className="icon-btn" disabled={running || audio.key >= KEY_MAX} onClick={() => setA({ key: audio.key + 1 })} aria-label="升一個 Key">+</button>
            {audio.key !== 0 && <button className="linkbtn" disabled={running} onClick={() => setA({ key: 0 })}>回原 Key</button>}
          </div>
          <div className="kx-row">
            <span className="kx-lab">導唱</span>
            <input type="range" min={0} max={100} step={5} value={audio.guide} disabled={running} aria-label="導唱"
              onChange={e => setA({ guide: Number(e.target.value) })} style={{ maxWidth: 280 }} />
            <b className="kx-key">{audio.guide}%</b>
          </div>
        </div>

        <div className="kx-row kx-out">
          <span className="kx-lab">存到</span>
          <span className="kx-path" title={settings.outDir}>{settings.outDir}</span>
          {desktop() && <button className="btn sm" onClick={pickDir} disabled={running}>更改</button>}
        </div>
        <div className="kx-row"><span className="kx-lab">檔名</span><b className="kx-name">{settings.outName}</b></div>
        <label className="check">
          <input type="checkbox" checked={settings.alsoLrc} disabled={running}
            onChange={async e => onSettings(await api.saveKaraoke(slug, { alsoLrc: e.target.checked }))} />
          同時輸出 .lrc 歌詞檔（下次同一首歌可以直接匯入）
        </label>

        {running && job && (
          <div className="ks-box">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span>{job.message}…</span>
              <span className="muted small">{Math.round(job.progress)}%{job.eta != null ? ` · 約剩 ${Math.max(1, Math.round(job.eta))} 秒` : ''}</span>
            </div>
            <div className="bar"><div style={{ width: `${Math.max(2, job.progress)}%` }} /></div>
          </div>
        )}
        {job?.state === 'cancelled' && <div className="notice warn">已取消。</div>}
        {err && <div className="notice error">{err}</div>}
        {done && job?.result && (
          <div className="kx-done">
            <div className="row"><span className="ok-badge kx-ok"><Check size={20} /></span>
              <b>完成！用了 {Math.round(job.result.seconds)} 秒{job.result.encoder === 'h264_nvenc' ? '（顯卡編碼）' : ''}</b></div>
            {job.result.files.map(f => (
              <div key={f} className="kx-file">
                <span className="kx-path">{f.split(/[\\/]/).pop()}</span>
                <button className="btn sm" onClick={() => api.open(f)}>{f.toLowerCase().endsWith('.mp4') ? '播放' : '開啟'}</button>
              </div>
            ))}
            <button className="btn sm" onClick={() => api.open(job.result!.files[0].replace(/[\\/][^\\/]+$/, ''))}><Folder />開啟資料夾</button>
          </div>
        )}

        <div className="kx-foot">
          <button className="btn" onClick={onBack} disabled={running}>上一步：字幕樣式</button>
          <div className="spacer" />
          {running
            ? <button className="btn" onClick={() => job && api.cancelKaraoke(job.id)}>取消</button>
            : <button className="btn primary big" onClick={start}>{done ? '再輸出一次' : '開始輸出'}</button>}
        </div>
      </div>
    </div>
  )
}
