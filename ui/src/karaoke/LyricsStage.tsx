/** 卡拉字幕預覽畫面（16:9）：兩行交替、提前顯示、整句均分掃色、前奏倒數點點。 */
import { stageAt, type TimedLine } from './lyricsView'

export default function LyricsStage({ lines, time, width }: { lines: TimedLine[]; time: number; width: number }) {
  const view = stageAt(lines, time)
  const h = Math.round((width * 9) / 16)
  const fs = Math.max(14, Math.round(width * 0.046))
  return (
    <div className="kstage" style={{ width, height: h }}>
      <div className="kstage-plate" style={{ padding: `${fs * 0.45}px ${fs * 1.1}px`, gap: fs * 0.25 }}>
        {[0, 1].map(slot => {
          const s = view.slots[slot]
          const cd = view.countdown && view.countdown.slot === slot ? view.countdown.dots : 0
          return (
            <div key={slot} className="kline-wrap" style={{ alignItems: slot === 0 ? 'flex-start' : 'flex-end', minHeight: fs * 1.9 }}>
              <div className="kdots" style={{ fontSize: fs * 0.42, height: fs * 0.5 }} aria-hidden>
                {cd > 0 && '●'.repeat(cd) + '○'.repeat(3 - cd)}
              </div>
              {s && (
                <div className="kline" style={{ fontSize: fs }}>
                  <span className="kline-base">{s.text}</span>
                  <span className="kline-sung" style={{ clipPath: `inset(0 ${(1 - s.sweep) * 100}% 0 0)` }}>{s.text}</span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
