import { useEffect, useRef, useState } from 'react'
import { api, type Job, type RenderResult, type Status } from './api'
import type { PreviewEngine } from './audio/engine'
import LoadScreen from './screens/LoadScreen'
import AnalyzeScreen from './screens/AnalyzeScreen'
import PreviewScreen, { defaultSettings, type PreviewSettings } from './screens/PreviewScreen'
import DoneScreen from './screens/DoneScreen'
import KaraokeTab from './karaoke/KaraokeTab'
import { UpdateDialog, UpdateFooter, UpdatePill, UpdateResult, useUpdate } from './Update'

type Screen =
  | { name: 'load'; error?: string }
  | { name: 'analyze'; job: Job }
  | { name: 'preview'; slug: string }
  | { name: 'done'; slug: string; result: RenderResult; seconds: number }

export type EngineRef = { slug: string; engine: PreviewEngine } | null

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [screen, setScreen] = useState<Screen>({ name: 'load' })
  // 預覽引擎與設定放在這一層：從「完成」回去再調一版時，不用重新載入、設定也還在
  const engine = useRef<EngineRef>(null)
  const [settings, setSettings] = useState<PreviewSettings>(defaultSettings)
  const upd = useUpdate()
  const [showUpdate, setShowUpdate] = useState(false)
  const [tab, setTab] = useState<'accomp' | 'karaoke'>('accomp')

  useEffect(() => { api.status().then(setStatus).catch(() => setStatus(null)) }, [])


  const dropEngine = () => {
    engine.current?.engine.dispose()
    engine.current = null
  }

  const openSong = (slug: string) => {
    if (engine.current?.slug !== slug) {
      dropEngine()
      setSettings(defaultSettings)
    }
    setScreen({ name: 'preview', slug })
  }

  const goHome = (error?: string) => {
    dropEngine()
    setScreen({ name: 'load', error })
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">DENKI 伴奏工具</div>
        <nav className="tabs">
          <button className={`tab ${tab === 'accomp' ? 'on' : ''}`} onClick={() => setTab('accomp')}>伴奏處理</button>
          <button className={`tab ${tab === 'karaoke' ? 'on' : ''}`}
            onClick={() => { engine.current?.engine.pause(); setTab('karaoke') }}
            disabled={screen.name === 'analyze'} title={screen.name === 'analyze' ? '分析完成後再切換' : undefined}>
            卡拉影片
          </button>
        </nav>
        <div className="spacer" />
        <UpdatePill info={upd.info} onClick={() => setShowUpdate(true)} />
        {status && (
          <div className={`pill ${status.device === 'cuda' ? '' : 'cpu'}`} title={status.deviceName}>
            <span className="dot" />
            <span>{status.device === 'cuda' ? `GPU 加速：${status.deviceName.replace('NVIDIA GeForce ', '')}` : 'CPU 模式'}</span>
          </div>
        )}
      </header>
      <UpdateResult info={upd.info} onAck={() => {
        api.ackUpdate().catch(() => {})
        if (upd.info) upd.setInfo({ ...upd.info, lastResult: null })
      }} />

      {tab === 'karaoke' && <KaraokeTab onGoAccomp={() => { setTab('accomp'); goHome() }} />}
      {tab === 'accomp' && screen.name === 'load' && (
        <LoadScreen
          error={screen.error}
          status={status}
          onJob={job => setScreen({ name: 'analyze', job })}
          onOpen={openSong}
        />
      )}
      {tab === 'accomp' && screen.name === 'analyze' && (
        <AnalyzeScreen
          job={screen.job}
          status={status}
          onDone={slug => openSong(slug)}
          onBack={goHome}
        />
      )}
      {tab === 'accomp' && screen.name === 'preview' && (
        <PreviewScreen
          key={screen.slug}
          slug={screen.slug}
          status={status}
          engineRef={engine}
          settings={settings}
          onSettings={setSettings}
          onBack={() => goHome()}
          onRendered={(result, seconds) => setScreen({ name: 'done', slug: screen.slug, result, seconds })}
        />
      )}
      {tab === 'accomp' && screen.name === 'done' && (
        <DoneScreen
          result={screen.result}
          seconds={screen.seconds}
          onAgain={() => setScreen({ name: 'preview', slug: screen.slug })}
          onNext={() => goHome()}
        />
      )}
      {(tab === 'karaoke' || screen.name === 'load') && (
        <UpdateFooter info={upd.info} checking={upd.checking} onCheck={upd.refresh} onOpen={() => setShowUpdate(true)} />
      )}
      {showUpdate && upd.info?.latest && (
        <UpdateDialog info={upd.info} setInfo={upd.setInfo} onClose={() => {
          setShowUpdate(false)
          if (upd.info?.state === 'error') upd.refresh()
        }} />
      )}
    </div>
  )
}
