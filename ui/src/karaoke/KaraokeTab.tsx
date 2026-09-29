/** 卡拉影片分頁（第二期）：① 選歌與歌詞 → ② 對時間 → ③ 字幕樣式 → ④ 輸出 */
import { useCallback, useEffect, useState } from 'react'
import { api, fmtTime, MODE_LABEL, type KaraokeSettings, type Lyrics, type Song } from '../api'
import ExportStep from './ExportStep'
import LyricsStep from './LyricsStep'
import StyleStep from './StyleStep'
import TimingStep from './TimingStep'

type Step = 'lyrics' | 'timing' | 'style' | 'export'

const STEPS: { id: Step; label: string }[] = [
  { id: 'lyrics', label: '選歌與歌詞' },
  { id: 'timing', label: '對時間' },
  { id: 'style', label: '字幕樣式' },
  { id: 'export', label: '輸出' },
]

export default function KaraokeTab({ onGoAccomp }: { onGoAccomp: () => void }) {
  const [songs, setSongs] = useState<Song[] | null>(null)
  const [slug, setSlug] = useState<string | null>(null)
  const [step, setStep] = useState<Step>('lyrics')
  const [lyr, setLyr] = useState<{ slug: string; lyrics: Lyrics | null; titleGuess: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [ks, setKs] = useState<KaraokeSettings | null>(null)

  const reload = useCallback(() => api.songs().then(list => {
    setSongs(list)
    setSlug(cur => cur && list.some(s => s.slug === cur) ? cur : (list.find(s => s.analyzed)?.slug ?? null))
  }).catch(e => setErr((e as Error).message)), [])
  useEffect(() => { reload() }, [reload])

  // 換歌 → 讀這首歌的歌詞
  useEffect(() => {
    if (!slug) return
    setLyr(null)
    setKs(null)
    setStep('lyrics')
    let stale = false
    api.lyrics(slug).then(r => { if (!stale) setLyr({ slug, ...r }) }).catch(e => !stale && setErr((e as Error).message))
    api.karaoke(slug).then(r => { if (!stale) setKs(r) }).catch(e => !stale && setErr((e as Error).message))
    return () => { stale = true }
  }, [slug])

  const song = songs?.find(s => s.slug === slug) ?? null
  const ready = lyr && lyr.slug === slug
  const onSaved = useCallback((l: Lyrics) => {
    setLyr(cur => (cur ? { ...cur, lyrics: l } : cur))
    setSongs(list => list?.map(s => (s.slug === slug ? { ...s, lyricsLines: l.lines.length } : s)) ?? list)
  }, [slug])

  return (
    <main className="main kmain">
      <div className="kbar">
        <div className="kbar-song">
          {song ? (
            <>
              <b>{song.title}</b>
              <span className="muted small">
                {song.hasVideo ? '影片' : '音檔'} · {fmtTime(song.duration)}
                {song.lastMode ? ` · 人聲來源：${MODE_LABEL[song.lastMode]}` : ''}
              </span>
            </>
          ) : <span className="muted">先選一首歌</span>}
        </div>
        <div className="spacer" />
        <div className="ksteps">
          {STEPS.map((s, i) => {
            const idx = STEPS.findIndex(x => x.id === step)
            const hasLyrics = !!(ready && lyr?.lyrics?.lines.length)
            const blocked = s.id !== 'lyrics' && (!hasLyrics || (s.id !== 'timing' && !ks))
            const cls = s.id === step ? 'on' : i < idx ? 'done' : ''
            return (
              <div key={s.id} className="ksteps-item">
                {i > 0 && <span className="ksteps-sep" />}
                <button className={`kstep ${cls}`}
                  disabled={blocked} title={blocked ? '先選好歌詞' : undefined}
                  onClick={() => setStep(s.id)}>
                  <b>{i < idx ? '✓' : i + 1}</b>{s.label}
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {err && <div className="notice error">{err}</div>}

      {step === 'lyrics' && (
        <div className="klayout">
          <div className="ksongs">
            <div className="muted small">選一首已經分析過的歌（對時間要用到分離出的人聲）</div>
            {songs && !songs.length && (
              <div className="notice warn">還沒有處理過的歌。先到「伴奏處理」拖入一首歌分析。</div>
            )}
            <div className="ksongs-list">
              {songs?.map(s => {
                const disabled = !s.analyzed
                return (
                  <button key={s.slug} className={`ksong ${s.slug === slug ? 'on' : ''}`} disabled={disabled}
                    onClick={() => setSlug(s.slug)} title={disabled ? '中間檔被清除了，要先到「伴奏處理」重新分析' : s.title}>
                    <span className="ksong-t">{s.title}</span>
                    <span className="muted small">
                      {s.hasVideo ? '影片' : '音檔'} · {fmtTime(s.duration)}
                      {disabled ? ' · 需要重新分析' : s.lyricsLines ? ` · 歌詞 ${s.lyricsLines} 句` : ''}
                    </span>
                  </button>
                )
              })}
            </div>
            {songs?.some(s => !s.analyzed) && (
              <button className="linkbtn" onClick={onGoAccomp}>到「伴奏處理」分析新的歌</button>
            )}
          </div>
          {song && ready ? (
            <LyricsStep key={song.slug} song={song} existing={lyr!.lyrics} titleGuess={lyr!.titleGuess}
              onUse={l => { onSaved(l); setStep('timing') }}
              onContinue={() => setStep('timing')} />
          ) : (
            <div className="card ks-card ks-empty muted">{song ? '讀取中…' : '左邊選一首歌'}</div>
          )}
        </div>
      )}

      {step === 'timing' && song && ready && lyr!.lyrics && (
        <TimingStep key={song.slug} song={song} initial={lyr!.lyrics}
          onBack={() => setStep('lyrics')} onSaved={onSaved} onNext={ks ? () => setStep('style') : undefined} />
      )}
      {step === 'style' && song && ready && lyr!.lyrics && ks && (
        <StyleStep key={song.slug} song={song} lyrics={lyr!.lyrics} settings={ks} onSettings={setKs}
          onBack={() => setStep('timing')} onNext={() => setStep('export')} />
      )}
      {step === 'export' && song && ready && lyr!.lyrics && ks && (
        <ExportStep key={song.slug} song={song} lyrics={lyr!.lyrics} settings={ks} onSettings={setKs}
          onBack={() => setStep('style')} />
      )}
    </main>
  )
}
