/** 線條圖示（不用 emoji、不用外部圖示庫，離線也正常） */
const s = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }

export const Check = ({ size = 16, color = '#fff' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...s} stroke={color} strokeWidth={3} aria-hidden><path d="M5 12l5 5L20 7" /></svg>
)
export const Play = ({ size = 18, color = '#fff' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={color} aria-hidden><path d="M7 5l12 7-12 7z" /></svg>
)
export const Pause = ({ size = 18, color = '#fff' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={color} aria-hidden><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
)
export const Back = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" {...s} strokeWidth={2} aria-hidden><path d="M15 6l-6 6 6 6" /></svg>
)
export const Upload = () => (
  <svg width="44" height="44" viewBox="0 0 24 24" {...s} stroke="#0F766E" strokeWidth={1.6} aria-hidden>
    <path d="M12 16V4" /><path d="M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" />
  </svg>
)
export const Film = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...s} stroke="#0F766E" strokeWidth={1.8} aria-hidden>
    <rect x="3" y="5" width="18" height="14" rx="2" /><path d="M10 9l5 3-5 3z" />
  </svg>
)
export const Note = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" {...s} stroke="#0F766E" strokeWidth={1.8} aria-hidden>
    <path d="M9 18V6l10-2v12" /><circle cx="6" cy="18" r="3" /><circle cx="16" cy="16" r="3" />
  </svg>
)
export const Folder = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...s} strokeWidth={1.8} aria-hidden>
    <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
  </svg>
)
export const Loop = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...s} strokeWidth={2} aria-hidden>
    <path d="M17 2l4 4-4 4" /><path d="M3 11V9a3 3 0 013-3h15" /><path d="M7 22l-4-4 4-4" /><path d="M21 13v2a3 3 0 01-3 3H3" />
  </svg>
)
