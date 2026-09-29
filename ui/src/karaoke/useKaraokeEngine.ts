/** 卡拉影片分頁共用：載入預覽播放引擎，並每一格畫面更新播放位置 */
import { useEffect, useState } from 'react'
import type { ModeId } from '../api'
import { PreviewEngine, type LoadProgress } from '../audio/engine'

export function useKaraokeEngine(slug: string, mode: ModeId | null, key: number, guide: number) {
  const [engine, setEngine] = useState<PreviewEngine | null>(null)
  const [loadP, setLoadP] = useState<LoadProgress | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!mode) return
    let cancelled = false
    let made: PreviewEngine | null = null
    setEngine(null)
    PreviewEngine.load(slug, mode, setLoadP)
      .then(e => {
        if (cancelled) return e.dispose()
        made = e
        e.setRate(1)
        e.setCompensation(0)
        e.onEnded = () => setPlaying(false)
        setEngine(e)
      })
      .catch(e => !cancelled && setErr(`播放器載入失敗：${(e as Error).message}`))
    return () => { cancelled = true; made?.dispose() }
  }, [slug, mode])

  useEffect(() => { engine?.setKey(key) }, [engine, key])
  useEffect(() => { engine?.setGuide(guide / 100) }, [engine, guide])

  useEffect(() => {
    if (!engine) return
    let id = 0
    const tick = () => {
      setPos(engine.position)
      setPlaying(engine.playing)
      id = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(id)
  }, [engine])

  return { engine, loadP, err, pos, playing }
}
