/** ④ 輸出：伴奏設定（模式、Key、導唱）、存放位置、同時輸出 .lrc → 1080p MP4（字幕燒進畫面） */
import { useEffect, useRef, useState } from 'react'
import { api, desktop, keyLabel, MODE_LABEL, type KaraokeJob, type KaraokeSettings, type Lyrics, type Song } from '../api'
import { Check, Folder } from '../icons'
import { fmtOffset } from './lyricsView'

const BG_LABEL: Record<string, string> = { video: '原影片', color: '單色', image: '自選圖片', cover: '模糊封面' }

export default function ExportStep({ song, lyrics, settings, onSettings, onBack }: {
  song: Song
  lyrics: Lyrics
  settings: KaraokeSettings
  onSettings: (s: KaraokeSettings) => void
  onBack: () => void
}) {
  const slug = song.slug
  const [job, setJob] = useState<KaraokeJob | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

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
          <div><span>伴奏</span>{MODE_LABEL[settings.audio.mode]} · {keyLabel(settings.audio.key)} · 導唱 {settings.audio.guide}%</div>
        </div>
        <div className="muted small">內容都在上一步「樣式與預覽」決定好了，要改就按「上一步」。</div>

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
          <button className="btn" onClick={onBack} disabled={running}>上一步：樣式與預覽</button>
          <div className="spacer" />
          {running
            ? <button className="btn" onClick={() => job && api.cancelKaraoke(job.id)}>取消</button>
            : <button className="btn primary big" onClick={start}>{done ? '再輸出一次' : '開始輸出'}</button>}
        </div>
      </div>
    </div>
  )
}
