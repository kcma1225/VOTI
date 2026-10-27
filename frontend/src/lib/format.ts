/* zh-TW number / unit / date formatting. Every number shown in the UI goes through here. */
const LOCALE = 'zh-TW'
const nf0 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })
const nf1 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const nf2 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const compact = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 })
const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' })
const dateFmt = new Intl.DateTimeFormat(LOCALE, { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'Asia/Taipei' })
const dateTimeFmt = new Intl.DateTimeFormat(LOCALE, {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Taipei',
})

const dash = '—'
const ok = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n)

export const fmtInt = (n: number | null | undefined) => (ok(n) ? nf0.format(n) : dash)
export const fmt1 = (n: number | null | undefined) => (ok(n) ? nf1.format(n) : dash)
export const fmt2 = (n: number | null | undefined) => (ok(n) ? nf2.format(n) : dash)
export const fmtCompact = (n: number | null | undefined) => (ok(n) ? compact.format(n) : dash)
export const fmtPct = (r: number | null | undefined, digits = 0) =>
  ok(r) ? `${(r * 100).toFixed(digits)}%` : dash

/** Hours with a sensible unit: < 1 h → 分鐘, else 小時 with 1 decimal (≥ 100 h: integer). */
export function fmtHours(sec: number | null | undefined): string {
  if (!ok(sec)) return dash
  const h = sec / 3600
  if (h < 1) return `${nf0.format(Math.round(sec / 60))} 分鐘`
  return `${h >= 100 ? nf0.format(h) : nf1.format(h)} 小時`
}
/** Just the number of hours (for charts / stat tiles that print the unit separately). */
export const hoursNum = (sec: number) => sec / 3600

/** Clock-style duration: 4:05, 1:02:03 */
export function fmtClock(sec: number | null | undefined): string {
  if (!ok(sec)) return dash
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}
/** Precise timestamp for segment boundaries: 1:02.35 */
export function fmtTimestamp(sec: number | null | undefined): string {
  if (!ok(sec)) return dash
  const m = Math.floor(sec / 60)
  const s = sec - m * 60
  return `${m}:${s.toFixed(2).padStart(5, '0')}`
}
export const fmtSec = (sec: number | null | undefined, digits = 1) => (ok(sec) ? `${sec.toFixed(digits)} 秒` : dash)
export const fmtDb = (n: number | null | undefined) => (ok(n) ? `${nf1.format(n)} dB` : dash)
export const fmtKHz = (hz: number | null | undefined) => (ok(hz) ? `${nf1.format(hz / 1000)} kHz` : dash)
export const fmtMos = (n: number | null | undefined) => (ok(n) ? nf2.format(n) : dash)

export function fmtBytes(b: number | null | undefined): string {
  if (!ok(b)) return dash
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = b
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${i === 0 ? nf0.format(v) : nf1.format(v)} ${u[i]}`
}

export const fmtDate = (iso: string | null | undefined) => (iso ? dateFmt.format(new Date(iso)) : dash)
export const fmtDateTime = (iso: string | null | undefined) => (iso ? dateTimeFmt.format(new Date(iso)) : dash)

export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return dash
  const diff = (new Date(iso).getTime() - now) / 1000
  const a = Math.abs(diff)
  if (a < 45) return '剛剛'
  if (a < 3600) return rtf.format(Math.round(diff / 60), 'minute')
  if (a < 86400) return rtf.format(Math.round(diff / 3600), 'hour')
  if (a < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day')
  if (a < 86400 * 365) return rtf.format(Math.round(diff / (86400 * 30)), 'month')
  return rtf.format(Math.round(diff / (86400 * 365)), 'year')
}

/** Remaining time like "約 3 分鐘" */
export function fmtEta(sec: number | null | undefined): string {
  if (!ok(sec)) return dash
  if (sec < 60) return `約 ${Math.max(1, Math.round(sec))} 秒`
  if (sec < 3600) return `約 ${Math.round(sec / 60)} 分鐘`
  return `約 ${nf1.format(sec / 3600)} 小時`
}
