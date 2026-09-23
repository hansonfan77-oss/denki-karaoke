declare module 'signalsmith-stretch' {
  export interface StretchSchedule {
    output?: number
    active?: boolean
    input?: number
    rate?: number
    semitones?: number
    tonalityHz?: number
    formantSemitones?: number
    formantCompensation?: boolean
    formantBaseHz?: number
    loopStart?: number
    loopEnd?: number
  }
  export interface StretchNode extends AudioWorkletNode {
    inputTime: number
    schedule(obj: StretchSchedule): void
    start(when?: number): void
    stop(when?: number): void
    addBuffers(buffers: Float32Array[]): Promise<number>
    dropBuffers(toSeconds?: number): Promise<unknown>
    setUpdateInterval(seconds: number, callback?: (t: number) => void): void
    latency(): number
    configure(cfg: Record<string, unknown>): void
  }
  export default function SignalsmithStretch(ctx: BaseAudioContext, options?: AudioWorkletNodeOptions): Promise<StretchNode>
}
