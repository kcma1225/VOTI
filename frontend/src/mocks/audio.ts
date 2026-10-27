/* Synthesises a speech-like 16 kHz mono WAV (voiced harmonics with syllable envelope + noise). */
import { rngFor } from './data'

export function synthWav(seed: number, durSec: number, snrDb = 20, f0Base = 140): ArrayBuffer {
  const sr = 16000
  const n = Math.max(1, Math.round(Math.min(30, durSec) * sr))
  const r = rngFor(seed)
  const buf = new ArrayBuffer(44 + n * 2)
  const v = new DataView(buf)
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)))
  w(0, 'RIFF')
  v.setUint32(4, 36 + n * 2, true)
  w(8, 'WAVE')
  w(12, 'fmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true)
  v.setUint16(22, 1, true)
  v.setUint32(24, sr, true)
  v.setUint32(28, sr * 2, true)
  v.setUint16(32, 2, true)
  v.setUint16(34, 16, true)
  w(36, 'data')
  v.setUint32(40, n * 2, true)

  // syllable plan: alternating voiced chunks (120–260 ms) and gaps; occasional pauses
  const env = new Float32Array(n)
  let t = Math.round(0.25 * sr)
  while (t < n) {
    const len = Math.round(r.range(0.12, 0.26) * sr)
    const amp = r.range(0.45, 1)
    for (let i = 0; i < len && t + i < n; i++) {
      const x = i / len
      env[t + i] = amp * Math.sin(Math.PI * x) ** 0.6
    }
    t += len + Math.round((r.chance(0.12) ? r.range(0.25, 0.6) : r.range(0.02, 0.08)) * sr)
  }
  const noiseAmp = 0.35 * 10 ** (-snrDb / 20)
  let phase = 0
  let f0 = f0Base
  for (let i = 0; i < n; i++) {
    if (i % 400 === 0) f0 = f0Base * (1 + 0.18 * Math.sin(i / 2600) + r.normal(0, 0.02))
    phase += (2 * Math.PI * f0) / sr
    let s = 0
    for (let h = 1; h <= 8; h++) s += Math.sin(phase * h) / (h * 1.3)
    const sample = 0.32 * env[i] * s + noiseAmp * (r.next() * 2 - 1)
    v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, sample)) * 32767, true)
  }
  return buf
}
