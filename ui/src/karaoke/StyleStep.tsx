/** ③ 字幕樣式與背景：右邊改設定，左邊邊播邊看（跟輸出的 MP4 同一套版面） */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, fmtTime, keyLabel, MODE_LABEL, songFileUrl, type KaraokeSettings, type Lyrics, type ModeId, type Song } from '../api'
import { Pause, Play } from '../icons'
import LyricsStage from './LyricsStage'
import { timed, type KStyle } from './lyricsView'
import { useKaraokeEngine } from './useKaraokeEngine'

const SUNG_SWATCHES = ['#4FD1C5', '#FFD166', '#FF8FAB', '#7AA2FF', '#FF6B6B']
const UNSUNG_SWATCHES = ['#FFFFFF', '#FFF3C4', '#D6E4FF']
const BG_COLORS = ['#1F2D36', '#000000', '#2B1E3A', '#10302A', '#3A2418']

type BgKind = KaraokeSettings['background']['kind']

export default function StyleStep({ song, lyrics, settings, onSettings, onBack, onNext }: {
  song: Song
  lyrics: Lyrics
  settings: KaraokeSettings
  onSettings: (s: KaraokeSettings) => void
  onBack: () => void
  onNext: () => void
}) {
  const slug = song.slug
  const [style, setStyle] = useState<KStyle>(settings.style)
  const [bg, setBg] = useState(settings.background)
  const [audio, setAudio] = useState(settings.audio)
  const [dirty, setDirty] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [imgStamp, setImgStamp] = useState(String(Date.now()))
  const { engine, loadP, err: engineErr, pos, playing } = useKaraokeEngine(slug, audio.mode, audio.key, audio.guide)
  const leftRef = useRef<HTMLDivElement>(null)
  const [stageW, setStageW] = useState(720)
  const fileInput = useRef<HTMLInputElement>(null)
  const video = useRef<HTMLVideoElement>(null)

  useLayoutEffect(() => {
    const el = leftRef.current
    if (!el) return
    const fit = () => setStageW(Math.max(320, Math.floor(Math.min(el.clientWidth, ((el.clientHeight - 200) * 16) / 9))))
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 改了就自動存（也記成下一首歌的預設樣式）
  const setS = (patch: Partial<KStyle>) => { setStyle(s => ({ ...s, ...patch })); setDirty(true) }
  const setB = (patch: Partial<typeof bg>) => { setBg(b => ({ ...b, ...patch })); setDirty(true) }
  const setA = (patch: Partial<typeof audio>) => { setAudio(a => ({ ...a, ...patch })); setDirty(true) }
  const save = useCallback(async () => {
    try {
      onSettings(await api.saveKaraoke(slug, { style, background: bg, audio }))
      setDirty(false)
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [slug, style, bg, audio, onSettings])
  useEffect(() => {
    if (!dirty) return
    const t = setTimeout(save, 500)
    return () => clearTimeout(t)
  }, [dirty, save])

  const upload = async (f: File) => {
    setErr(null)
    try {
      const s = await api.uploadBg(slug, f)
      onSettings(s)
      setImgStamp(String(Date.now()))
      setBg(b => ({ ...b, kind: 'image' }))
      setDirty(true)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  // 背景實際會用哪一種（auto 的判斷跟後端一樣）
  const kind: Exclude<BgKind, 'auto'> = useMemo(() => {
    let k: BgKind = bg.kind
    if (k === 'auto') k = settings.hasVideo ? 'video' : settings.hasCover ? 'cover' : 'color'
    if (k === 'video' && !settings.hasVideo) k = settings.hasCover ? 'cover' : 'color'
    if (k === 'cover' && !settings.hasCover) k = 'color'
    if (k === 'image' && !settings.hasImage) k = 'color'
    return k
  }, [bg.kind, settings])

  // 影片背景：跟著播放位置同步（靜音，聲音由播放器出）
  useEffect(() => {
    const v = video.current
    if (!v) return
    if (Math.abs(v.currentTime - pos) > 0.3) v.currentTime = pos
    if (playing && v.paused) v.play().catch(() => undefined)
    if (!playing && !v.paused) v.pause()
  }, [pos, playing, kind])

  const background = kind === 'video'
    ? (settings.videoPreview
      ? <video ref={video} className="kbg" src={songFileUrl(slug, 'video')} muted playsInline preload="auto" />
      : <div className="kbg kbg-note">（這個影片格式在預覽裡播不出來，輸出時會用原影片畫面）</div>)
    : kind === 'cover' ? <img className="kbg kbg-blur" src={songFileUrl(slug, 'cover')} alt="" />
      : kind === 'image' ? <ImageBg src={songFileUrl(slug, 'bg-image', imgStamp)} fit={bg.fit ?? 'fill'} scale={stageW / 1920} />
        : <div className="kbg" style={{ background: bg.color }} />

  const tl = useMemo(() => timed(lyrics.lines, lyrics.offset), [lyrics])
  const firstLine = tl[0]?.t ?? 0
  const duration = engine?.duration ?? song.duration ?? 0

  const bgOptions: { id: BgKind; label: string; ok: boolean; why?: string }[] = [
    { id: 'video', label: '原影片', ok: settings.hasVideo, why: '原檔是音檔' },
    { id: 'color', label: '單色', ok: true },
    { id: 'image', label: '自選圖片', ok: true },
    { id: 'cover', label: '模糊封面', ok: settings.hasCover, why: '這首歌沒有內嵌封面' },
  ]

  return (
    <div className="kt">
      <div className="kt-left" ref={leftRef}>
        <LyricsStage lines={tl} time={pos} width={stageW} style={style} background={background} />
        <div className="card kt-transport">
          <button className="play" onClick={() => engine?.toggle()} disabled={!engine} aria-label={playing ? '暫停' : '播放'}>
            {playing ? <Pause /> : <Play />}
          </button>
          <span className="time">{fmtTime(pos)}</span>
          <input type="range" min={0} max={duration || 1} step={0.1} value={pos} aria-label="播放位置"
            onChange={e => engine?.seek(Number(e.target.value))} disabled={!engine} />
          <span className="time muted">{fmtTime(duration)}</span>
          <button className="btn sm" disabled={!engine} onClick={() => engine?.seek(Math.max(0, firstLine - 5))}>到第一句</button>
        </div>
        <div className="card kaudio">
          <b className="kaudio-h">伴奏</b>
          <div className="seg">
            {settings.analyzedModes.map((m: ModeId) => (
              <button key={m} className={audio.mode === m ? 'on' : ''} onClick={() => setA({ mode: m })}>{MODE_LABEL[m]}</button>
            ))}
          </div>
          <span className="kaudio-lab">Key</span>
          <button className="icon-btn" disabled={audio.key <= -12} onClick={() => setA({ key: audio.key - 1 })} aria-label="降一個 Key">−</button>
          <b className="kaudio-val">{keyLabel(audio.key)}</b>
          <button className="icon-btn" disabled={audio.key >= 12} onClick={() => setA({ key: audio.key + 1 })} aria-label="升一個 Key">+</button>
          <span className="kaudio-lab">導唱</span>
          <input type="range" min={0} max={100} step={5} value={audio.guide} aria-label="導唱"
            onChange={e => setA({ guide: Number(e.target.value) })} />
          <b className="kaudio-val">{audio.guide}%</b>
        </div>
        {!engine && <div className={`notice ${engineErr ? 'error' : 'warn'}`}>{engineErr ?? `載入歌曲中…${loadP?.stage === 'download' ? ` ${Math.round(loadP.ratio * 100)}%` : ''}`}</div>}
        <div className="muted small">這裡就是最終成品：畫面、字幕、伴奏（模式、Key、導唱）都跟輸出的 MP4 一樣。</div>
      </div>

      <div className="card kt-right ksty">
        <div className="ksty-body">
          <div className="ksty-h">字幕</div>
          <div className="ksty-grid">
            <label className="ksty-f">字型
              <select value={style.font} onChange={e => setS({ font: e.target.value })}>
                {settings.fonts.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
              </select>
            </label>
            <label className="ksty-f">大小 <b>{style.size}</b>
              <input type="range" min={36} max={110} step={2} value={style.size} onChange={e => setS({ size: Number(e.target.value) })} />
            </label>
            <div className="ksty-f">未唱顏色
              <Swatches value={style.unsung} options={UNSUNG_SWATCHES} onPick={c => setS({ unsung: c })} />
            </div>
            <div className="ksty-f">已唱顏色
              <Swatches value={style.sung} options={SUNG_SWATCHES} onPick={c => setS({ sung: c })} />
            </div>
            <label className="ksty-f">描邊 <b>{style.outline}</b>
              <input type="range" min={0} max={8} step={1} value={style.outline} onChange={e => setS({ outline: Number(e.target.value) })} />
            </label>
            <div className="ksty-f">位置
              <div className="seg">
                <button className={style.position === 'bottom' ? 'on' : ''} onClick={() => setS({ position: 'bottom' })}>下方</button>
                <button className={style.position === 'middle' ? 'on' : ''} onClick={() => setS({ position: 'middle' })}>中間</button>
              </div>
            </div>
          </div>
          <div className="ksty-checks">
            <label className="check"><input type="checkbox" checked={style.countdown} onChange={e => setS({ countdown: e.target.checked })} />前奏／間奏倒數 ●●●</label>
            <label className="check"><input type="checkbox" checked={style.sweep} onChange={e => setS({ sweep: e.target.checked })} />掃色</label>
          </div>

          <div className="ksty-h">字幕底板</div>
          <div className="ksty-grid">
            <div className="ksty-f">底板顏色
              <div className="seg">
                {(['none', 'black', 'white'] as const).map(p => (
                  <button key={p} className={style.plate === p ? 'on' : ''} onClick={() => setS({ plate: p })}>
                    {p === 'none' ? '不用' : p === 'black' ? '黑' : '白'}
                  </button>
                ))}
              </div>
            </div>
            <label className="ksty-f">透明度 <b>{style.plateOpacity}%</b>
              <input type="range" min={0} max={100} step={5} value={style.plateOpacity} disabled={style.plate === 'none'}
                onChange={e => setS({ plateOpacity: Number(e.target.value) })} />
            </label>
          </div>

          <div className="ksty-h">背景</div>
          <div className="seg ksty-bg">
            {bgOptions.map(o => (
              <button key={o.id} className={kind === o.id ? 'on' : ''} disabled={!o.ok} title={o.ok ? undefined : o.why}
                onClick={() => (o.id === 'image' && !settings.hasImage ? fileInput.current?.click() : setB({ kind: o.id }))}>
                {o.label}
              </button>
            ))}
          </div>
          {kind === 'color' && <Swatches value={bg.color} options={BG_COLORS} onPick={c => setB({ color: c, kind: 'color' })} />}
          {kind === 'image' && (
            <div className="ksty-fit">
              <span className="small muted">圖片顯示方式</span>
              <div className="seg">
                {([['fill', '填滿（裁切邊緣）'], ['fit', '完整顯示'], ['center', '置中（原尺寸）']] as const).map(([f, label]) => (
                  <button key={f} className={(bg.fit ?? 'fill') === f ? 'on' : ''} onClick={() => setB({ fit: f })}>{label}</button>
                ))}
              </div>
              <button className="linkbtn" onClick={() => fileInput.current?.click()}>換一張圖片</button>
            </div>
          )}
          <input ref={fileInput} type="file" accept=".jpg,.jpeg,.png,.webp,.bmp" hidden
            onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
          {err && <div className="notice error">{err}</div>}
        </div>
        <div className="kt-foot">
          <button className="btn" onClick={() => { if (dirty) save(); onBack() }}>上一步：對時間</button>
          <span className="small muted kt-save">{dirty ? '稍後自動儲存' : '已自動儲存'}</span>
          <div className="spacer" />
          <button className="btn primary" onClick={async () => { if (dirty) await save(); onNext() }}>下一步：輸出</button>
        </div>
      </div>
    </div>
  )
}

function Swatches({ value, options, onPick }: { value: string; options: string[]; onPick: (c: string) => void }) {
  const custom = !options.includes(value.toUpperCase())
  return (
    <div className="swatches">
      {options.map(c => (
        <button key={c} className={`swatch ${value.toUpperCase() === c ? 'on' : ''}`} style={{ background: c }}
          onClick={() => onPick(c)} aria-label={`顏色 ${c}`} />
      ))}
      <label className={`swatch swatch-pick ${custom ? 'on' : ''}`} style={custom ? { background: value } : undefined} title="自訂顏色">
        <input type="color" value={value} onChange={e => onPick(e.target.value.toUpperCase())} aria-label="自訂顏色" />
        {!custom && '+'}
      </label>
    </div>
  )
}

/** 自選圖片背景：填滿（裁切）／完整顯示／置中（原尺寸），後兩種空白處用同一張圖模糊補滿（跟輸出一樣） */
function ImageBg({ src, fit, scale }: { src: string; fit: 'fill' | 'fit' | 'center'; scale: number }) {
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null)
  if (fit === 'fill') return <img className="kbg" src={src} alt="" />
  let fg: React.CSSProperties = { width: '100%', height: '100%', objectFit: 'contain' }
  if (fit === 'center' && nat) {
    const k = Math.min(1, 1920 / nat.w, 1080 / nat.h) * scale   // 比畫面大才縮小，否則原尺寸
    fg = { width: nat.w * k, height: nat.h * k }
  }
  return (
    <>
      <img className="kbg kbg-blur" src={src} alt="" />
      <div className="kbg kbg-center">
        <img src={src} alt="" style={fg} onLoad={e => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
      </div>
    </>
  )
}
