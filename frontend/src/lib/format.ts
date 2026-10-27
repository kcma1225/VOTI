/* Locale-aware number / unit / date formatting (zh-TW or en-US, always Asia/Taipei time).
 * Every number shown in the UI goes through here; formatters are cached per locale. */
import { getLang, intlLocale } from '@/i18n/lang'
import { zh as zhCommon, en as enCommon } from '@/i18n/messages/common'

const TZ = 'Asia/Taipei'
type Fmts = ReturnType<typeof build>
function build(locale: string) {
  return {
    nf0: new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }),
    nf1: new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
    nf2: new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    compact: new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }),
    rtf: new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }),
    date: new Intl.DateTimeFormat(locale, { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: TZ }),
    dateTime: new Intl.DateTimeFormat(locale, {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ,
    }),
  }
}
const cache = new Map<string, Fmts>()
function F(): Fmts {
  const loc = intlLocale()
  let f = cache.get(loc)
  if (!f) cache.set(loc, (f = build(loc)))
  return f
}
const U = () => (getLang() === 'en' ? enCommon : zhCommon).units

const dash = '—'
const ok = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n)

export const fmtInt = (n: number | null | undefined) => (ok(n) ? F().nf0.format(n) : dash)
export const fmt1 = (n: number | null | undefined) => (ok(n) ? F().nf1.format(n) : dash)
export const fmt2 = (n: number | null | undefined) => (ok(n) ? F().nf2.format(n) : dash)
export const fmtCompact = (n: number | null | undefined) => (ok(n) ? F().compact.format(n) : dash)
export const fmtPct = (r: number | null | undefined, digits = 0) =>
  ok(r) ? `${(r * 100).toFixed(digits)}%` : dash

/** Hours with a sensible unit: < 1 h → minutes, else hours with 1 decimal (≥ 100 h: integer). */
export function fmtHours(sec: number | null | undefined): string {
  if (!ok(sec)) return dash
  const h = sec / 3600
  if (h < 1) {
    return U().minutes(F().nf0.format(Math.round(sec / 60)))
  }
  const n = h >= 100 ? F().nf0.format(h) : F().nf1.format(h)
  return U().hours(n)
}
/** The unit printed after hoursNum() in stat tiles / chart axes. */
export const hoursUnit = () => U().hoursUnit
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
export const fmtSec = (sec: number | null | undefined, digits = 1) => (ok(sec) ? U().seconds(sec.toFixed(digits)) : dash)
export const fmtDb = (n: number | null | undefined) => (ok(n) ? `${F().nf1.format(n)} dB` : dash)
export const fmtKHz = (hz: number | null | undefined) => (ok(hz) ? `${F().nf1.format(hz / 1000)} kHz` : dash)
export const fmtMos = (n: number | null | undefined) => (ok(n) ? F().nf2.format(n) : dash)

export function fmtBytes(b: number | null | undefined): string {
  if (!ok(b)) return dash
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = b
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${i === 0 ? F().nf0.format(v) : F().nf1.format(v)} ${u[i]}`
}

export const fmtDate = (iso: string | null | undefined) => (iso ? F().date.format(new Date(iso)) : dash)
export const fmtDateTime = (iso: string | null | undefined) => (iso ? F().dateTime.format(new Date(iso)) : dash)

export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return dash
  const diff = (new Date(iso).getTime() - now) / 1000
  const a = Math.abs(diff)
  if (a < 45) return U().justNow
  if (a < 3600) return F().rtf.format(Math.round(diff / 60), 'minute')
  if (a < 86400) return F().rtf.format(Math.round(diff / 3600), 'hour')
  if (a < 86400 * 30) return F().rtf.format(Math.round(diff / 86400), 'day')
  if (a < 86400 * 365) return F().rtf.format(Math.round(diff / (86400 * 30)), 'month')
  return F().rtf.format(Math.round(diff / (86400 * 365)), 'year')
}

/** Remaining time like "~3 min" */
export function fmtEta(sec: number | null | undefined): string {
  if (!ok(sec)) return dash
  if (sec < 60) return U().etaSec(String(Math.max(1, Math.round(sec))))
  if (sec < 3600) return U().etaMin(String(Math.round(sec / 60)))
  return U().etaHours(F().nf1.format(sec / 3600))
}
