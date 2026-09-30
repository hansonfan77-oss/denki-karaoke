/**
 * 卡拉字幕預覽畫面（16:9）：兩行交替、提前顯示、整句均分掃色、前奏倒數點點、底板。
 * 版面數字跟輸出 MP4（karaoke/kvideo.py）同一套，都以 1920×1080 為準再等比縮小。
 */
import type { ReactNode } from 'react'
import type { RubyItem } from '../api'
import { fitSize, geometry, plateVisible, stageAt, VW, type KStyle, type TimedLine } from './lyricsView'

export const DEFAULT_STYLE: KStyle = {
  font: 'jhenghei', size: 64, unsung: '#FFFFFF', sung: '#4FD1C5', outline: 3,
  position: 'bottom', plate: 'black', plateOpacity: 50, countdown: true, sweep: true, sweepWord: true,
  furigana: false, rubySize: 45,
}

export const FONT_CSS: Record<string, string> = {
  jhenghei: '"Microsoft JhengHei", "Microsoft JhengHei UI", sans-serif',
  yugothic: '"Yu Gothic", "Yu Gothic UI", "Meiryo", sans-serif',
  meiryo: '"Meiryo", "Meiryo UI", sans-serif',
  mingliu: '"PMingLiU", "MingLiU", serif',
}

/** 一句歌詞：漢字上面加假名（假名放在漢字正上方，不影響字的排列，跟輸出的 MP4 一樣） */
function withRuby(text: string, ruby: RubyItem[] | null, rubyPx: number, stroke: number): ReactNode {
  const items = (ruby ?? []).filter(r => r.r)
  if (!items.length) return text
  const out: ReactNode[] = []
  let p = 0
  for (const r of items) {
    if (r.s < p) continue
    if (r.s > p) out.push(text.slice(p, r.s))
    out.push(
      <span key={r.s} className="kr">{text.slice(r.s, r.e)}
        <span className="krt" style={{ fontSize: rubyPx, WebkitTextStroke: stroke ? `${stroke}px #000` : undefined }}>{r.r}</span>
      </span>,
    )
    p = r.e
  }
  if (p < text.length) out.push(text.slice(p))
  return out
}

export default function LyricsStage({ lines, time, width, style = DEFAULT_STYLE, background }: {
  lines: TimedLine[]
  time: number
  width: number
  style?: KStyle
  background?: ReactNode
}) {
  const k = width / VW
  const h = Math.round((width * 9) / 16)
  const g = geometry(style)
  const view = stageAt(lines, time, true)   // 逐字／均分在「對時間」逐句切換
  const plateColor = style.plate === 'white' ? '255,255,255' : '0,0,0'
  const stroke = style.outline * k
  const family = FONT_CSS[style.font] ?? FONT_CSS.jhenghei

  return (
    <div className="kstage" style={{ width, height: h }}>
      {background}
      {style.plate !== 'none' && plateVisible(view) && (
        <div className="kstage-plate2" style={{
          top: g.plateTop * k, height: g.plateH * k,
          background: `rgba(${plateColor},${style.plateOpacity / 100})`,
        }} />
      )}
      {[0, 1].map(slot => {
        const s = view.slots[slot]
        const cd = style.countdown && view.countdown && view.countdown.slot === slot ? view.countdown.dots : 0
        const side = slot === 0 ? { left: g.padH * k } : { right: g.padH * k }
        const size = s ? fitSize(s.text, g.fs, g.padH) : g.fs
        const lineTop = (g.slots[slot].line + (g.lineH - size * 1.25) / 2) * k
        const sweep = !s ? 0 : style.sweep ? s.sweep : (time >= lines[s.index].t ? 1 : 0)
        const ruby = s && style.furigana ? lines[s.index].ruby : null
        const rubyPx = size * (style.rubySize ?? 45) / 100 * k
        const rubyStroke = style.outline ? Math.max(1, Math.round(style.outline * 0.6)) * 2 * k : 0
        const room = ruby?.length ? `${rubyPx * 1.4}px` : '0px'   // 掃色遮罩往上、往左右多留，假名才會一起變色
        return (
          <div key={slot}>
            {cd > 0 && (
              <div className="kdots2" style={{ ...side, top: g.slots[slot].dots * k, fontSize: g.fs * 0.42 * k, fontFamily: family,
                WebkitTextStroke: `${Math.max(1, style.outline - 1) * k}px #000` }} aria-hidden>
                {'●'.repeat(cd) + '○'.repeat(3 - cd)}
              </div>
            )}
            {s && (
              <div className="kline2" style={{ ...side, top: lineTop, fontSize: size * k, fontFamily: family }}>
                <span className="kline-base" style={{ color: style.unsung, WebkitTextStroke: stroke ? `${stroke * 2}px #000` : undefined }}>{withRuby(s.text, ruby, rubyPx, rubyStroke)}</span>
                <span className="kline-sung" style={{
                  color: style.sung, WebkitTextStroke: stroke ? `${stroke * 2}px #000` : undefined,
                  clipPath: sweep <= 0 ? 'inset(0 100% 0 0)' : `inset(-${room} ${sweep >= 1 ? `-${room}` : `${(1 - sweep) * 100}%`} 0 -${room})`,
                }}>{withRuby(s.text, ruby, rubyPx, rubyStroke)}</span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
