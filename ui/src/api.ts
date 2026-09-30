/** 跟本機 Python 後端溝通。所有網址都是相對路徑，打包後由後端直接提供。 */

export type ModeId = 'standard' | 'hq' | 'harmony'

export interface ModeInfo {
  id: ModeId
  label: string
  desc: string
  available: boolean
  reason: string
  sizeMb: number | null
}

export interface Status {
  version: string
  device: 'cuda' | 'cpu'
  deviceName: string
  ffmpeg: boolean
  rubberband: boolean
  modes: ModeInfo[]
  defaultMode: ModeId
  beatsAvailable?: boolean
  root: string
}

/** 預備拍用：速度與第一拍（原曲時間軸，秒） */
export interface Beats {
  bpm: number
  firstBeat: number
  musicStart: number
  manual: boolean
  auto: { bpm: number; firstBeat: number }
}

export interface RenderRecord { mode?: ModeId; key: number; guide: number; tempo: number; format: string; file: string; at: string }

export interface SongMode { analyzed: boolean; seconds: number | null; device: string | null; compensationDb: number }

export interface Song {
  slug: string
  title: string
  analyzed: boolean
  analyzedModes: ModeId[]
  lastMode: ModeId | null
  modes: Record<ModeId, SongMode>
  hasVideo: boolean
  duration: number | null
  source: string
  sourceExists: boolean
  renders: RenderRecord[]
  outDir: string
  cacheBytes: number
  lyricsLines?: number
}

// ---------------- 第 4 批：卡拉影片（歌詞與對時間）
export interface WordTime { text: string; t: number; end: number }
export interface LyricLine { t: number; end: number | null; text: string; words?: WordTime[] | null; even?: boolean | null; ruby?: RubyItem[] | null }
/** 漢字讀音：text[s..e) 標 r（r 空字串＝不標）；m＝手改過 */
export interface RubyItem { s: number; e: number; r: string; m?: boolean }
export type LyricsSource = 'lrclib' | 'ai' | 'lrc' | 'manual'
export interface Lyrics {
  source: LyricsSource
  lang: string
  offset: number
  lines: LyricLine[]
  lrclib?: { id: number; trackName: string; artistName: string } | null
  updated_at?: string
}
export interface LrclibResult {
  id: number
  trackName: string
  artistName: string
  albumName: string
  duration: number | null
  durationDiff: number | null
  instrumental: boolean
  synced: boolean
  lines: LyricLine[]
  plain: string
}
export interface KaraokeSettings {
  style: import('./karaoke/lyricsView').KStyle
  background: { kind: 'auto' | 'video' | 'color' | 'image' | 'cover'; color: string; fit: 'fill' | 'fit' | 'center' }
  audio: { mode: ModeId; key: number; guide: number }
  alsoLrc: boolean
  fonts: { id: string; label: string; family: string }[]
  resolvedBg: { kind: 'video' | 'color' | 'image' | 'cover'; color: string }
  hasVideo: boolean
  videoPreview: boolean
  hasCover: boolean
  hasImage: boolean
  outName: string
  outDir: string
  analyzedModes: ModeId[]
  compensationDb: Record<string, number>
}
export interface KaraokeJob {
  id: string
  slug: string
  state: 'running' | 'done' | 'error' | 'cancelled'
  progress: number
  message: string
  elapsed: number
  eta: number | null
  error: string | null
  result: { files: string[]; seconds: number; encoder: string; background: string } | null
}
export interface AlignStatus { packages: boolean; model: boolean; modelBytes: number; modelName: string; device: 'cuda' | 'cpu' }
export interface AlignJob {
  id: string
  slug: string
  state: 'running' | 'done' | 'error' | 'cancelled'
  stage: 'prepare' | 'download' | 'load' | 'align' | 'save'
  progress: number
  message: string
  elapsed: number
  error: string | null
  lyrics: Lyrics | null
}

export interface Job {
  id: string
  source: string
  mode: ModeId
  modeLabel: string
  state: 'running' | 'done' | 'error' | 'cancelled'
  stage: 'read' | 'separate' | 'save'
  progress: number
  message: string
  elapsed: number
  eta: number | null
  slug: string | null
  error: string | null
  device: string
}

/** 程式內更新（第 3 批） */
export interface UpdateInfo {
  current: string
  state: 'idle' | 'checking' | 'downloading' | 'installing' | 'restarting' | 'error'
  available: boolean
  latest: { tag: string; version: string; notes: string; published: string } | null
  checkedAt: number | null
  error: string | null
  bytes: number
  total: number | null
  depsTodo: string[] | null
  torchChange: boolean
  enabled: boolean
  disabledReason: string
  lastResult: { ok: boolean; from: string; to: string; message: string; at: string } | null
}

export interface RenderFile { path: string; name: string; size: number; kind: 'video' | 'audio' }
export interface RenderResult { files: RenderFile[]; outDir: string | null }
export type OutFormat = 'video' | 'mp3' | 'wav'

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || data.detail || `錯誤 ${res.status}`)
  return data as T
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})

const enc = encodeURIComponent

export const api = {
  status: () => call<Status>('api/status'),
  songs: () => call<Song[]>('api/songs'),
  song: (slug: string) => call<Song>(`api/songs/${enc(slug)}`),
  analyze: (path: string, mode?: ModeId) => call<Job>('api/analyze', json({ path, mode })),
  analyzeSong: (slug: string, mode: ModeId) => call<Job>(`api/songs/${enc(slug)}/analyze`, json({ mode })),
  /** 上傳（瀏覽器拿不到檔案路徑時）。用 XHR 才拿得到上傳進度。 */
  upload: (file: File, mode?: ModeId, onProgress?: (loaded: number, total: number) => void) =>
    new Promise<Job>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('PUT', `api/upload?name=${enc(file.name)}${mode ? `&mode=${mode}` : ''}`)
      xhr.upload.onprogress = e => onProgress?.(e.loaded, e.lengthComputable ? e.total : file.size)
      xhr.onload = () => {
        let data: { detail?: string; error?: string } & Partial<Job> = {}
        try { data = JSON.parse(xhr.responseText) } catch { /* 空回應 */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data as Job)
        else reject(new Error(data.error || data.detail || `上傳失敗（${xhr.status}）`))
      }
      xhr.onerror = () => reject(new Error('上傳中斷，請再試一次，或改用「選擇檔案」按鈕'))
      xhr.send(file)
    }),
  job: (id: string) => call<Job>(`api/jobs/${id}`),
  cancel: (id: string) => call<Job>(`api/jobs/${id}/cancel`, { method: 'POST' }),
  render: (slug: string, body: {
    mode: ModeId; key: number; guide: number; format: OutFormat; alsoMp3: boolean; tempo: number; compensate: boolean
    countin: number
  }) => call<RenderResult>(`api/songs/${enc(slug)}/render`, json(body)),
  renderName: (slug: string, mode: ModeId, key: number, guide: number, tempo: number, format: OutFormat, countin = 0) =>
    call<{ name: string; outDir: string }>(
      `api/render-name?slug=${enc(slug)}&mode=${mode}&key=${key}&guide=${guide}&tempo=${tempo}&format=${format}&countin=${countin}`),
  beats: (slug: string) => call<Beats>(`api/songs/${enc(slug)}/beats`),
  setBeats: (slug: string, body: { bpm?: number; firstBeat?: number; taps?: number[] }) =>
    call<Beats>(`api/songs/${enc(slug)}/beats`, json(body, 'PUT')),
  resetBeats: (slug: string) => call<Beats>(`api/songs/${enc(slug)}/beats`, { method: 'DELETE' }),
  cache: () => call<{ bytes: number; songs: number }>('api/cache'),
  clearSong: (slug: string) => call<{ freed: number }>(`api/songs/${enc(slug)}/cache`, { method: 'DELETE' }),
  clearAll: () => call<{ freed: number }>('api/cache', { method: 'DELETE' }),
  settings: () => call<{ outDir?: string | null }>('api/settings'),
  setOutDir: (outDir: string | null) => call<{ outDir?: string | null }>('api/settings', json({ outDir })),
  open: (path: string) => call<{ ok: boolean }>('api/open', json({ path })),
  update: (refresh = false) => call<UpdateInfo>(`api/update${refresh ? '?refresh=1' : ''}`),
  startUpdate: () => call<UpdateInfo>('api/update/start', { method: 'POST' }),
  ackUpdate: () => call<{ ok: boolean }>('api/update/ack', { method: 'POST' }),
  // 第 4 批
  lyrics: (slug: string) => call<{ lyrics: Lyrics | null; titleGuess: string }>(`api/songs/${enc(slug)}/lyrics`),
  saveLyrics: (slug: string, body: { lines: LyricLine[]; offset: number; source: LyricsSource; lang?: string; lrclib?: Lyrics['lrclib'] }) =>
    call<Lyrics>(`api/songs/${enc(slug)}/lyrics`, json(body, 'PUT')),
  deleteLyrics: (slug: string) => call<{ ok: boolean }>(`api/songs/${enc(slug)}/lyrics`, { method: 'DELETE' }),
  searchLyrics: (title: string, artist: string, duration: number | null) =>
    call<{ results: LrclibResult[] }>(`api/lyrics/search?title=${enc(title)}&artist=${enc(artist)}${duration ? `&duration=${duration}` : ''}`),
  parseLrc: (text: string) => call<{ lines: LyricLine[]; lang: string }>('api/lyrics/parse-lrc', json({ text })),
  alignStatus: () => call<AlignStatus>('api/align/status'),
  align: (slug: string, text: string, lang?: string) => call<AlignJob>(`api/songs/${enc(slug)}/align`, json({ text, lang })),
  furigana: (slug: string, redo = false) => call<Lyrics>(`api/songs/${enc(slug)}/furigana`, json({ redo })),
  refine: (slug: string, line?: number) => call<AlignJob>(`api/songs/${enc(slug)}/refine`, json(line === undefined ? {} : { line })),
  alignJob: (id: string) => call<AlignJob>(`api/align-jobs/${id}`),
  cancelAlign: (id: string) => call<AlignJob>(`api/align-jobs/${id}/cancel`, { method: 'POST' }),
  // 第 5 批
  karaoke: (slug: string) => call<KaraokeSettings>(`api/songs/${enc(slug)}/karaoke`),
  saveKaraoke: (slug: string, body: Partial<Pick<KaraokeSettings, 'style' | 'background' | 'audio' | 'alsoLrc'>>) =>
    call<KaraokeSettings>(`api/songs/${enc(slug)}/karaoke`, json(body, 'PUT')),
  uploadBg: (slug: string, file: File) =>
    call<KaraokeSettings>(`api/songs/${enc(slug)}/bg-image?name=${enc(file.name)}`, { method: 'PUT', body: file }),
  renderKaraoke: (slug: string, outDir?: string | null) =>
    call<KaraokeJob>(`api/songs/${enc(slug)}/karaoke-render`, json({ outDir: outDir || null })),
  karaokeJob: (id: string) => call<KaraokeJob>(`api/karaoke-jobs/${id}`),
  cancelKaraoke: (id: string) => call<KaraokeJob>(`api/karaoke-jobs/${id}/cancel`, { method: 'POST' }),
}

export const songFileUrl = (slug: string, kind: 'bg-image' | 'cover' | 'video', v = '') =>
  `api/songs/${enc(slug)}/${kind}${v ? `?v=${enc(v)}` : ''}`

export const stemUrl = (slug: string, mode: ModeId, name: 'no_vocals' | 'vocals') =>
  `api/songs/${enc(slug)}/stems/${mode}/${name}`

/** 輪詢一個分析工作直到結束 */
export async function waitJob(id: string, onTick: (j: Job) => void, isStopped: () => boolean = () => false): Promise<Job> {
  for (;;) {
    let j: Job | null = null
    try { j = await api.job(id) } catch { /* 暫時連不上就下次再試 */ }
    if (isStopped()) throw new Error('stopped')
    if (j) {
      onTick(j)
      if (j.state !== 'running') return j
    }
    await new Promise(r => setTimeout(r, 500))
  }
}

// ---------------- pywebview 橋接（桌面視窗才有；一般瀏覽器開發時沒有）
interface PyApi { pick_file(): Promise<string | null>; pick_folder(): Promise<string | null> }
declare global {
  interface Window {
    pywebview?: { api: PyApi }
    __denkiDroppedPath?: (path: string) => void
  }
}
export const desktop = () => window.pywebview?.api

export function fmtTime(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return '--:--'
  const s = Math.max(0, Math.round(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function fmtSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`
}

export const keyLabel = (k: number) => (k === 0 ? '原 Key' : k > 0 ? `+${k}` : `−${-k}`)

export const MODE_LABEL: Record<ModeId, string> = { standard: '標準', hq: '高品質', harmony: '保留和聲' }
