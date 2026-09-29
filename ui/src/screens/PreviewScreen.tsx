/** ③ 邊聽邊調：分離模式、Key、導唱、速度、預備拍都可以邊聽邊換；滿意了再輸出一個版本 */
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react'
import {
  api, desktop, fmtTime, keyLabel, MODE_LABEL, waitJob,
  type Beats, type Job, type ModeId, type OutFormat, type RenderResult, type Song, type Status,
} from '../api'
import { PreviewEngine, type LoadProgress } from '../audio/engine'
import { Back, Loop, Pause, Play } from '../icons'

export interface PreviewSettings {
  mode: ModeId | null    // null = 用這首歌上次的模式
  key: number
  guide: number          // 0~100
  tempo: number          // 70~110
  compensate: boolean    // 音量補償
  format: OutFormat | null
  alsoMp3: boolean
  applyTempo: boolean
  countin: boolean       // 預備拍：預設不勾，換歌就重設（只有一開始就進歌的曲子才需要）
  countinBars: 1 | 2
}
export const defaultSettings: PreviewSettings = {
  mode: null, key: 0, guide: 20, tempo: 100, compensate: true, format: null, alsoMp3: true, applyTempo: false,
  countin: false, countinBars: 1,
}

const NUDGE = 0.05   // 早一點／晚一點：每按一下移動 0.05 秒
const TAPS_NEEDED = 8
const fmtBeat = (sec: number) => `${Math.floor(sec / 60)}:${(sec % 60).toFixed(2).padStart(5, '0')}`

const KEY_MIN = -12, KEY_MAX = 12
const ALL_MODES: ModeId[] = ['standard', 'hq', 'harmony']

export default function PreviewScreen({ slug, status, engineRef, settings, onSettings, onBack, onRendered }: {
  slug: string
  status: Status | null
  engineRef: MutableRefObject<{ slug: string; engine: PreviewEngine } | null>
  settings: PreviewSettings
  onSettings: (s: PreviewSettings) => void
  onBack: () => void
  onRendered: (r: RenderResult, seconds: number) => void
}) {
  const [song, setSong] = useState<Song | null>(null)
  const [engine, setEngine] = useState<PreviewEngine | null>(
    engineRef.current?.slug === slug ? engineRef.current.engine : null)
  const [loadP, setLoadP] = useState<LoadProgress | null>(null)
  const [engineErr, setEngineErr] = useState<string | null>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [loop, setLoop] = useState<[number, number] | null>(engine?.loop ?? null)
  const [pendingA, setPendingA] = useState<number | null>(null)
  const [outName, setOutName] = useState<{ name: string; outDir: string } | null>(null)
  const [rendering, setRendering] = useState(false)
  const [renderErr, setRenderErr] = useState<string | null>(null)
  const [modeJob, setModeJob] = useState<Job | null>(null)
  const [modeErr, setModeErr] = useState<string | null>(null)
  const [beats, setBeats] = useState<Beats | null>(null)
  const [beatsBusy, setBeatsBusy] = useState(false)
  const [beatsErr, setBeatsErr] = useState<string | null>(null)
  const [taps, setTaps] = useState<number[] | null>(null)   // null = 不在點拍模式
  const beatsSave = useRef<number | undefined>(undefined)
  // 換模式時要接續的播放狀態
  const resume = useRef<{ pos: number; playing: boolean } | null>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const settingsRef = useRef(settings)
  settingsRef.current = settings
  const set = useCallback((patch: Partial<PreviewSettings>) => onSettings({ ...settingsRef.current, ...patch }), [onSettings])

  // 目前使用的模式：設定裡指定的（且已分析）→ 這首歌上次用的
  const mode: ModeId | null = song
    ? (settings.mode && song.modes[settings.mode]?.analyzed ? settings.mode : song.lastMode)
    : (engine?.mode ?? settings.mode)
  const format: OutFormat = settings.format ?? (song?.hasVideo ? 'video' : 'mp3')
  const tempoForOutput = format !== 'video' && settings.applyTempo ? settings.tempo : 100
  const compDb = song && mode ? song.modes[mode].compensationDb : 0
  const effectiveComp = settings.compensate ? compDb * (1 - settings.guide / 100) : 0
  // 預備拍只加在音檔；選影片時要勾「同時輸出 MP3」，那個 MP3 才會加
  const countinBlocked = format === 'video' && !settings.alsoMp3
  const countinOn = settings.countin && !countinBlocked && !!beats
  const beatsAvailable = status?.beatsAvailable !== false

  // ---------------- 載入
  const refreshSong = useCallback(() => api.song(slug).then(s => { if (alive.current) setSong(s); return s }), [slug])
  useEffect(() => { refreshSong().catch(e => setEngineErr(e.message)) }, [refreshSong])

  // 模式確定後載入預覽引擎（換模式時重新載入，播放位置接續）
  useEffect(() => {
    if (!mode) return
    if (engine && engine.mode === mode) return
    let cancelled = false
    if (engine) {
      resume.current = { pos: engine.position, playing: engine.playing }
      engine.dispose()
      engineRef.current = null
      setEngine(null)
    }
    setLoadP(null)
    setEngineErr(null)
    PreviewEngine.load(slug, mode, setLoadP)
      .then(e => {
        if (cancelled) return e.dispose()
        engineRef.current = { slug, engine: e }
        ;(window as unknown as { __denkiEngine?: PreviewEngine }).__denkiEngine = e // 自動測試用
        const st = settingsRef.current
        e.setKey(st.key)
        e.setGuide(st.guide / 100)
        e.setRate(st.tempo / 100)
        if (loop) e.setLoop(loop)
        const r = resume.current
        resume.current = null
        if (r) {
          e.seek(r.pos)
          if (r.playing) e.play()
        }
        setEngine(e)
      })
      .catch(e => !cancelled && setEngineErr(`預覽載入失敗：${e.message}。仍然可以直接輸出。`))
    return () => { cancelled = true }
  }, [slug, mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // 設定改變 → 即時套用到預覽
  useEffect(() => { engine?.setKey(settings.key) }, [engine, settings.key])
  useEffect(() => { engine?.setGuide(settings.guide / 100) }, [engine, settings.guide])
  useEffect(() => { engine?.setRate(settings.tempo / 100) }, [engine, settings.tempo])
  useEffect(() => { engine?.setCompensation(effectiveComp) }, [engine, effectiveComp])

  // 勾了預備拍才偵測速度（第一次約幾秒，之後讀紀錄）
  useEffect(() => {
    if (!settings.countin || beats || beatsBusy || !song?.analyzed) return
    setBeatsBusy(true)
    setBeatsErr(null)
    api.beats(slug)
      .then(b => alive.current && setBeats(b))
      .catch(e => alive.current && setBeatsErr((e as Error).message))
      .finally(() => alive.current && setBeatsBusy(false))
  }, [settings.countin, beats, beatsBusy, song, slug])

  // 離開畫面時暫停（引擎保留給「回去再調一版」）
  useEffect(() => () => { engineRef.current?.engine.pause() }, [engineRef])

  // 播放位置
  useEffect(() => {
    if (!engine) return
    engine.onEnded = () => setPlaying(false)
    let raf = 0
    const loopFn = () => {
      setPos(engine.position)
      setPlaying(engine.playing)
      raf = requestAnimationFrame(loopFn)
    }
    raf = requestAnimationFrame(loopFn)
    return () => cancelAnimationFrame(raf)
  }, [engine])

  // 檔名預覽
  useEffect(() => {
    if (!song || !mode) return
    const t = setTimeout(() => {
      api.renderName(slug, mode, settings.key, settings.guide, tempoForOutput, format, countinOn ? settings.countinBars : 0)
        .then(setOutName).catch(() => undefined)
    }, 150)
    return () => clearTimeout(t)
  }, [song, slug, mode, settings.key, settings.guide, tempoForOutput, format, countinOn, settings.countinBars])

  // 鍵盤：空白鍵播放/暫停、左右鍵前後 5 秒
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!engine || (e.target as HTMLElement).tagName === 'INPUT') return
      if (taps && e.code === 'Space') { e.preventDefault(); addTap(); return }
      if (taps && e.code === 'Escape') { setTaps(null); return }
      if (e.code === 'Space') { e.preventDefault(); engine.toggle() }
      if (e.code === 'ArrowLeft') engine.seek(engine.position - 5)
      if (e.code === 'ArrowRight') engine.seek(engine.position + 5)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [engine, taps]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------- 操作
  const changeKey = (k: number) => set({ key: Math.max(KEY_MIN, Math.min(KEY_MAX, k)) })

  const applyLoop = (l: [number, number] | null) => {
    setLoop(l)
    engine?.setLoop(l)
  }

  const abButton = () => {
    if (!engine) return
    if (loop) { applyLoop(null); setPendingA(null); return }
    if (pendingA == null) { setPendingA(engine.position); return }
    const b = engine.position
    applyLoop(b > pendingA ? [pendingA, b] : [b, pendingA])
    setPendingA(null)
  }

  const chooseMode = async (m: ModeId) => {
    if (!song || modeJob || m === mode) return
    setModeErr(null)
    if (song.modes[m]?.analyzed) {
      set({ mode: m })
      return
    }
    try {
      const job = await api.analyzeSong(slug, m)
      setModeJob(job)
      const done = await waitJob(job.id, j => alive.current && setModeJob(j), () => !alive.current)
      if (done.state === 'done') {
        await refreshSong()
        set({ mode: m })
      } else if (done.state === 'error') {
        setModeErr(`「${MODE_LABEL[m]}」分析失敗：${done.error}`)
      }
    } catch (e) {
      if ((e as Error).message !== 'stopped') setModeErr((e as Error).message)
    } finally {
      if (alive.current) setModeJob(null)
    }
  }

  // ---------------- 預備拍
  const saveBeats = (patch: { bpm?: number; firstBeat?: number }) => {
    if (!beats) return
    const next = { ...beats, ...patch }
    next.manual = next.bpm !== beats.auto.bpm || next.firstBeat !== beats.auto.firstBeat
    setBeats(next)
    window.clearTimeout(beatsSave.current)
    beatsSave.current = window.setTimeout(() => {
      api.setBeats(slug, patch.bpm !== undefined ? { bpm: next.bpm, firstBeat: next.firstBeat } : { firstBeat: next.firstBeat })
        .then(b => alive.current && setBeats(b))
        .catch(e => alive.current && setBeatsErr((e as Error).message))
    }, 300)
  }
  const nudge = (d: number) => beats && saveBeats({ firstBeat: Math.max(0, Math.round((beats.firstBeat + d) * 1000) / 1000) })
  const scaleBpm = (f: number) => {
    if (!beats) return
    const bpm = Math.round(beats.bpm * f * 100) / 100
    if (bpm >= 50 && bpm <= 220) saveBeats({ bpm })
  }
  const resetBeats = () => api.resetBeats(slug).then(b => alive.current && setBeats(b)).catch(() => undefined)

  const startTaps = async () => {
    if (!engine) return
    if (taps) { setTaps(null); return }
    setBeatsErr(null)
    if (!engine.playing) await engine.play()
    setTaps([])
  }
  const addTap = () => {
    if (!engine || !taps) return
    const next = [...taps, engine.position]
    if (next.length < TAPS_NEEDED) { setTaps(next); return }
    setTaps(null)
    api.setBeats(slug, { taps: next })
      .then(b => alive.current && setBeats(b))
      .catch(e => alive.current && setBeatsErr((e as Error).message))
  }

  const previewCountIn = () => {
    if (!engine || !beats) return
    setTaps(null)
    if (loop) applyLoop(null)
    engine.playWithCountIn(beats.bpm, beats.firstBeat, settings.countinBars).catch(e => setBeatsErr((e as Error).message))
  }

  const changeOutDir = async () => {
    const d = desktop()
    const dir = d ? await d.pick_folder() : window.prompt('輸出資料夾（留空 = 預設）', outName?.outDir ?? '')
    if (dir === null || dir === undefined || !mode) return
    await api.setOutDir(dir.trim() || null)
    setOutName(await api.renderName(slug, mode, settings.key, settings.guide, tempoForOutput, format, countinOn ? settings.countinBars : 0))
  }

  const doRender = async () => {
    if (!mode) return
    engine?.pause()
    setRendering(true)
    setRenderErr(null)
    const t0 = performance.now()
    try {
      const r = await api.render(slug, {
        mode, key: settings.key, guide: settings.guide, format,
        alsoMp3: format === 'video' && settings.alsoMp3, tempo: tempoForOutput, compensate: settings.compensate,
        countin: countinOn ? settings.countinBars : 0,
      })
      onRendered(r, (performance.now() - t0) / 1000)
    } catch (e) {
      setRenderErr((e as Error).message)
    } finally {
      setRendering(false)
    }
  }

  const formats: OutFormat[] = song?.hasVideo ? ['video', 'mp3', 'wav'] : ['mp3', 'wav']
  const fmtName: Record<OutFormat, string> = { video: '影片（只換音軌）', mp3: 'MP3', wav: 'WAV' }
  const duration = engine?.duration ?? song?.duration ?? 0
  const modeInfo = (m: ModeId) => status?.modes.find(x => x.id === m)

  return (
    <main className="main">
      <div className="row" style={{ gap: 12, alignItems: 'center' }}>
        <button className="icon-btn" onClick={onBack} aria-label="返回"><Back /></button>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {song?.title ?? slug}
          </div>
          <div className="muted small">
            {song?.hasVideo ? '影片' : '音檔'} · {fmtTime(duration)} · 已分析，邊聽邊調，滿意再輸出
          </div>
        </div>

        {/* 分離模式 */}
        <div className="modebar" role="radiogroup" aria-label="分離模式">
          <span className="label" style={{ marginRight: 4 }}>分離模式</span>
          {ALL_MODES.map(m => {
            const info = modeInfo(m)
            const analyzed = !!song?.modes[m]?.analyzed
            const running = modeJob?.mode === m
            const unavailable = info ? !info.available : false
            const sub = running
              ? (modeJob.stage === 'separate' && modeJob.progress > 0 ? `分析中 ${Math.floor(modeJob.progress)}%` : '準備中…')
              : analyzed ? '已分析'
                : unavailable ? '需要顯卡'
                  : m === 'standard' ? '約 30 秒' : '約 2 分鐘'
            return (
              <button key={m} role="radio" aria-checked={mode === m}
                className={`mode ${mode === m ? 'on' : ''} ${running ? 'running' : ''}`}
                disabled={!song || unavailable || (!!modeJob && !running)}
                title={unavailable ? info?.reason : info?.desc}
                onClick={() => chooseMode(m)}>
                <b>{MODE_LABEL[m]}</b>
                <span>{sub}</span>
                {running && <i style={{ width: `${modeJob.progress}%` }} />}
              </button>
            )
          })}
        </div>
      </div>

      {modeJob && modeJob.message.includes('下載') && (
        <div className="notice warn">{modeJob.message}（只有第一次需要，之後就不用再下載）</div>
      )}
      {modeErr && <div className="notice error">{modeErr}</div>}

      {/* 播放器 */}
      <section className="card player">
        {engine ? (
          <Waveform engine={engine} pos={pos} loop={loop} pendingA={pendingA}
            firstBeat={settings.countin && !countinBlocked && beats ? beats.firstBeat : null}
            onSeek={t => engine.seek(t)} onSelect={l => applyLoop(l)} />
        ) : (
          <div style={{ height: 88, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10 }}>
            <div className="muted small">
              {engineErr ?? (loadP?.stage === 'decode' ? '解碼中…' : loadP?.stage === 'prepare' ? '準備預覽…'
                : `載入${mode ? `「${MODE_LABEL[mode]}」` : ''}中間檔…`)}
            </div>
            {!engineErr && <div className="bar"><div style={{ width: `${Math.round((loadP?.ratio ?? 0) * 100)}%` }} /></div>}
          </div>
        )}
        <div className="transport">
          <button className="play" onClick={() => engine?.toggle()} disabled={!engine} aria-label={playing ? '暫停' : '播放'}>
            {playing ? <Pause /> : <Play />}
          </button>
          <span className="time">{fmtTime(pos)} / {fmtTime(duration)}</span>
          <div className="spacer" />
          <button className="btn sm" disabled={!engine} onClick={() => engine && engine.seek(engine.estimateChorus())}>
            跳到副歌（估計）
          </button>
          <button className={`btn sm ${loop || pendingA != null ? 'primary' : ''}`} disabled={!engine} onClick={abButton}>
            <Loop />
            {loop ? `A-B 重複：${fmtTime(loop[0])} – ${fmtTime(loop[1])}　✕`
              : pendingA != null ? `A = ${fmtTime(pendingA)}，再按一次設 B` : 'A-B 重複'}
          </button>
        </div>
        {!loop && pendingA == null && engine && (
          <div className="muted small">點波形跳到那裡；在波形上拖曳可以直接框出要重複的段落。空白鍵播放／暫停。</div>
        )}
      </section>

      <div className="grid2">
        {/* Key */}
        <section className="card panel">
          <div className="label">KEY（邊聽邊按，馬上生效）</div>
          <div className="keyrow">
            <button className="keybtn" onClick={() => changeKey(settings.key - 1)} disabled={settings.key <= KEY_MIN} aria-label="降一個 Key">−</button>
            <div className="keyval">
              <b>{settings.key === 0 ? '0' : settings.key > 0 ? `+${settings.key}` : `−${-settings.key}`}</b>
              <span className="muted small">半音</span>
            </div>
            <button className="keybtn" onClick={() => changeKey(settings.key + 1)} disabled={settings.key >= KEY_MAX} aria-label="升一個 Key">+</button>
          </div>
          <div className="chips">
            <button className={`chip ${settings.key === 0 ? 'on' : ''}`} onClick={() => changeKey(0)}>原 Key</button>
            <button className={`chip ${settings.key === -5 ? 'on' : ''}`} onClick={() => changeKey(-5)}>女轉男 −5</button>
            <button className={`chip ${settings.key === 5 ? 'on' : ''}`} onClick={() => changeKey(5)}>男轉女 +5</button>
          </div>
          <div className="small" style={{ textAlign: 'center', color: Math.abs(settings.key) > 6 ? 'var(--warn-text)' : 'var(--muted)' }}>
            範圍 ±12。超過 ±6 音色會開始變形，屬正常現象
          </div>
        </section>

        {/* 導唱 / 速度 / 音量補償 */}
        <section className="card panel">
          <div className="label">{mode === 'harmony' ? '導唱（保留多少主唱；和聲一直都在）' : '導唱（保留多少原唱）'}</div>
          <div>
            <div className="slider-head"><label htmlFor="guide">{mode === 'harmony' ? '主唱音量' : '原唱音量'}</label><b>{settings.guide}%</b></div>
            <input id="guide" type="range" min={0} max={100} value={settings.guide}
              onChange={e => set({ guide: Number(e.target.value) })} />
            <div className="slider-foot"><span>0% 純伴奏</span><span>100% 原曲</span></div>
          </div>
          <div>
            <div className="slider-head">
              <label htmlFor="tempo">速度（練習用）</label>
              <span style={{ fontWeight: 500 }}>{settings.tempo}%
                {settings.tempo !== 100 && <button className="chip" style={{ marginLeft: 8, height: 24 }} onClick={() => set({ tempo: 100 })}>還原</button>}
              </span>
            </div>
            <input id="tempo" type="range" min={70} max={110} step={5} value={settings.tempo}
              onChange={e => set({ tempo: Number(e.target.value) })} />
          </div>
          <label className="check" title="去掉人聲後整體會變小聲，把伴奏拉回原曲的音量（有防破音）">
            <input id="comp" type="checkbox" checked={settings.compensate} onChange={e => set({ compensate: e.target.checked })} />
            音量補償：拉回原曲音量
            <span className="muted" style={{ marginLeft: 'auto' }}>
              {settings.compensate ? (effectiveComp > 0.05 ? `+${effectiveComp.toFixed(1)} dB` : '不需要') : '關閉'}
            </span>
          </label>
        </section>
      </div>

      {/* 預備拍（第 2.3 批） */}
      <section className="card ci-card" aria-label="預備拍">
        <div className="ci-top">
          <label className={`ci-check ${countinBlocked || !beatsAvailable ? 'ci-dim' : ''}`}>
            <input id="countin" type="checkbox" checked={settings.countin} disabled={countinBlocked || !beatsAvailable}
              onChange={e => set({ countin: e.target.checked })} />
            預備拍（鼓棒互敲）
          </label>
          <div className={`seg ${settings.countin && !countinBlocked ? '' : 'ci-off'}`} role="radiogroup" aria-label="預備拍長度">
            {([1, 2] as const).map(b => (
              <button key={b} className={settings.countinBars === b ? 'on' : ''} onClick={() => set({ countinBars: b })}>{b} 小節</button>
            ))}
          </div>
          {settings.countin && !countinBlocked && (
            beatsBusy ? <span className="ci-det">偵測速度與第一拍中…（第一次會多等一下）</span>
              : beats ? (
                <div className="ci-det">
                  偵測到 <b>♩ {beats.bpm.toFixed(beats.bpm % 1 ? 1 : 0)}</b> BPM
                  <button className="chip mini" title="速度抓成兩倍了？減半" onClick={() => scaleBpm(0.5)}>÷2</button>
                  <button className="chip mini" title="速度抓成一半了？加倍" onClick={() => scaleBpm(2)}>×2</button>
                  · 4/4 · 第一拍 <b>{fmtBeat(beats.firstBeat)}</b>
                  {beats.manual
                    ? <button className="linkbtn" onClick={resetBeats}>已手動修正 · 還原 AI 偵測</button>
                    : <span className="ci-pill">AI 自動偵測</span>}
                </div>
              ) : null
          )}
        </div>
        {settings.countin && !countinBlocked && beats && (
          <div className="ci-top ci-row2">
            <span className="ci-note" style={{ fontWeight: 500 }}>第一拍微調</span>
            <button className="btn sm" onClick={() => nudge(-NUDGE)}>◀ 早一點</button>
            <button className="btn sm" onClick={() => nudge(NUDGE)}>晚一點 ▶</button>
            {taps
              ? <>
                <button className="btn sm primary tapbtn" onClick={addTap}>跟著音樂按空白鍵或點這裡（{taps.length}/{TAPS_NEEDED}）</button>
                <button className="linkbtn" onClick={() => setTaps(null)}>取消</button>
              </>
              : <button className="btn sm" disabled={!engine} onClick={startTaps}>點拍抓速度</button>}
            <button className="btn sm primary" disabled={!engine || !!taps} onClick={previewCountIn}>▶ 從預備拍試聽</button>
          </div>
        )}
        {beatsErr && settings.countin && <div className="notice error">{beatsErr}</div>}
        {!beatsAvailable
          ? <div className="ci-warn">這台電腦還沒安裝速度偵測元件，請點一次「更新並測試.bat」。</div>
          : countinBlocked
            ? <div className="ci-warn">影片輸出暫時不支援預備拍（之後會加）。要預備拍請勾「同時輸出 MP3」，或改選 MP3／WAV。</div>
            : settings.countin && beats
              ? <div className="ci-note">
                {format === 'video' ? '影片維持原長度；同時輸出的 MP3 會加上預備拍。' : '輸出時會在最前面加上鼓棒。'}
                速度滑桿調慢，預備拍也一起變慢。偵測不準（清唱開頭、弱起、速度自由）時用「早一點／晚一點」微調，或按「點拍抓速度」後跟著音樂點 {TAPS_NEEDED} 下。
              </div>
              : !settings.countin
                ? <div className="ci-note">歌曲一開始就進歌時使用：輸出前面加一小節鼓棒，練唱時才跟得上。</div>
                : null}
      </section>

      <div className="spacer" />

      {renderErr && <div className="notice error">輸出失敗：{renderErr}</div>}

      {/* 輸出列 */}
      <section className="card outbar">
        <div className="info">
          <div style={{ fontWeight: 500 }}>
            輸出：{fmtName[format]} · {mode ? MODE_LABEL[mode] : ''} · Key {keyLabel(settings.key)} · 導唱 {settings.guide}%
            {tempoForOutput !== 100 && ` · 速度 ${tempoForOutput}%`}
            {countinOn && (format === 'video' ? `（MP3 加預備拍 ${settings.countinBars} 小節）` : ` · 預備拍 ${settings.countinBars} 小節`)}
          </div>
          <div className="path" title={outName ? `${outName.outDir}\\${outName.name}` : ''}>
            {outName ? `${outName.outDir}${outName.outDir.includes('/') ? '/' : '\\'}${outName.name}` : '…'}
          </div>
        </div>
        <div className="seg" role="radiogroup" aria-label="輸出格式">
          {formats.map(f => (
            <button key={f} className={format === f ? 'on' : ''} onClick={() => set({ format: f })}>
              {f === 'video' ? '影片' : f.toUpperCase()}
            </button>
          ))}
        </div>
        {format === 'video' && (
          <label className="check"><input type="checkbox" checked={settings.alsoMp3}
            onChange={e => set({ alsoMp3: e.target.checked })} />同時輸出 MP3</label>
        )}
        {format !== 'video' && settings.tempo !== 100 && (
          <label className="check"><input type="checkbox" checked={settings.applyTempo}
            onChange={e => set({ applyTempo: e.target.checked })} />輸出也套用速度</label>
        )}
        <button className="btn sm" onClick={changeOutDir}>輸出位置</button>
        <button className="btn primary big" onClick={doRender} disabled={rendering || !song || !mode || !!modeJob}>
          {rendering ? '輸出中…' : '輸出這個版本'}
        </button>
      </section>
    </main>
  )
}

// ---------------- 波形
function Waveform({ engine, pos, loop, pendingA, firstBeat, onSeek, onSelect }: {
  engine: PreviewEngine
  pos: number
  loop: [number, number] | null
  pendingA: number | null
  firstBeat: number | null
  onSeek: (t: number) => void
  onSelect: (l: [number, number]) => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const drag = useRef<{ x0: number; t0: number; moved: boolean } | null>(null)
  const [dragRange, setDragRange] = useState<[number, number] | null>(null)

  useEffect(() => {
    const c = canvas.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    const w = c.clientWidth, h = c.clientHeight
    if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr }
    const g = c.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, w, h)

    const d = engine.duration
    const x = (t: number) => (t / d) * w
    const sel = dragRange ?? loop
    if (sel) {
      g.fillStyle = 'rgba(15,118,110,0.12)'
      g.fillRect(x(sel[0]), 0, x(sel[1]) - x(sel[0]), h)
    }
    const n = engine.peaks.length
    const bw = w / n
    const px = x(pos)
    for (let i = 0; i < n; i++) {
      const bx = i * bw
      const ph = Math.max(1.5, engine.peaks[i] * (h - 6))
      g.fillStyle = bx < px ? '#0F766E' : '#D6D1C7'
      g.fillRect(bx, (h - ph) / 2, Math.max(1, bw - 0.6), ph)
    }
    if (pendingA != null) {
      g.fillStyle = '#B45309'
      g.fillRect(x(pendingA) - 1, 0, 2, h)
    }
    if (firstBeat != null) {
      // 預備拍：第一拍的位置（橘色虛線）+ 標籤
      const fx = Math.max(1, x(firstBeat))
      g.strokeStyle = '#D97706'
      g.lineWidth = 2
      g.setLineDash([5, 4])
      g.beginPath(); g.moveTo(fx, 0); g.lineTo(fx, h); g.stroke()
      g.setLineDash([])
      const label = `預備拍 ●●●● → 第一拍 ${fmtBeat(firstBeat)}`
      g.font = '700 11px "Noto Sans TC", sans-serif'
      const tw = g.measureText(label).width
      const lx = Math.min(fx + 4, w - tw - 14)
      g.fillStyle = '#FFF7ED'
      g.strokeStyle = '#FBD38D'
      g.lineWidth = 1
      g.beginPath(); g.roundRect(lx, 3, tw + 12, 18, 5); g.fill(); g.stroke()
      g.fillStyle = '#B45309'
      g.fillText(label, lx + 6, 16)
    }
    g.fillStyle = '#1E1D1A'
    g.fillRect(px - 1, 0, 2, h)
  }, [engine, pos, loop, pendingA, dragRange, firstBeat])

  const timeAt = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect()
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * engine.duration
  }

  return (
    <canvas
      ref={canvas}
      className="wave"
      role="slider"
      aria-label="播放位置"
      aria-valuemin={0}
      aria-valuemax={Math.round(engine.duration)}
      aria-valuenow={Math.round(pos)}
      onPointerDown={e => {
        canvas.current!.setPointerCapture(e.pointerId)
        drag.current = { x0: e.clientX, t0: timeAt(e), moved: false }
      }}
      onPointerMove={e => {
        const d = drag.current
        if (!d) return
        if (Math.abs(e.clientX - d.x0) > 6) d.moved = true
        if (d.moved) {
          const t = timeAt(e)
          setDragRange(t > d.t0 ? [d.t0, t] : [t, d.t0])
        }
      }}
      onPointerUp={e => {
        const d = drag.current
        drag.current = null
        if (!d) return
        if (d.moved && dragRange) onSelect(dragRange)
        else onSeek(timeAt(e))
        setDragRange(null)
      }}
    />
  )
}
