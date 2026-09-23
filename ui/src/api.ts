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
}

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
