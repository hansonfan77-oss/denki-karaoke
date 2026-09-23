import { useEffect, useRef, useState } from 'react'
import { api, type Job, type RenderResult, type Status } from './api'
import type { PreviewEngine } from './audio/engine'
import LoadScreen from './screens/LoadScreen'
import AnalyzeScreen from './screens/AnalyzeScreen'
import PreviewScreen, { defaultSettings, type PreviewSettings } from './screens/PreviewScreen'
import DoneScreen from './screens/DoneScreen'

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
          <button className="tab on">伴奏處理</button>
          <button className="tab" disabled title="第二期">卡拉影片（第二期）</button>
        </nav>
        <div className="spacer" />
        {status && (
          <div className={`pill ${status.device === 'cuda' ? '' : 'cpu'}`} title={status.deviceName}>
            <span className="dot" />
            <span>{status.device === 'cuda' ? `GPU 加速：${status.deviceName.replace('NVIDIA GeForce ', '')}` : 'CPU 模式'}</span>
          </div>
        )}
      </header>

      {screen.name === 'load' && (
        <LoadScreen
          error={screen.error}
          status={status}
          onJob={job => setScreen({ name: 'analyze', job })}
          onOpen={openSong}
        />
      )}
      {screen.name === 'analyze' && (
        <AnalyzeScreen
          job={screen.job}
          status={status}
          onDone={slug => openSong(slug)}
          onBack={goHome}
        />
      )}
      {screen.name === 'preview' && (
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
      {screen.name === 'done' && (
        <DoneScreen
          result={screen.result}
          seconds={screen.seconds}
          onAgain={() => setScreen({ name: 'preview', slug: screen.slug })}
          onNext={() => goHome()}
        />
      )}
    </div>
  )
}
