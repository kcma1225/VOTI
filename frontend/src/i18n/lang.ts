/* Active UI language: a tiny external store (no React, no imports) so lib/format.ts and
 * lib/labels.ts can read it at call time without import cycles. */
export type Lang = 'zh-TW' | 'en'
export const LANGS: Lang[] = ['zh-TW', 'en']
const KEY = 'voti-lang'
const listeners = new Set<() => void>()

/** Browser preference: any zh* (incl. zh-CN) → zh-TW, anything else → en. */
export function detectLang(): Lang {
  try {
    const prefs = navigator.languages?.length ? navigator.languages : [navigator.language]
    for (const p of prefs) {
      if (!p) continue
      return p.toLowerCase().startsWith('zh') ? 'zh-TW' : 'en'
    }
  } catch {
    /* ignore */
  }
  return 'en'
}

function readStored(): Lang | null {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'zh-TW' || v === 'en' ? v : null
  } catch {
    return null
  }
}

function apply(l: Lang) {
  if (typeof document !== 'undefined') document.documentElement.lang = l === 'en' ? 'en' : 'zh-Hant-TW'
}

let current: Lang = typeof window !== 'undefined' ? (readStored() ?? detectLang()) : 'zh-TW'
apply(current)

export const getLang = () => current
/** BCP-47 tag for Intl formatters. */
export const intlLocale = () => (current === 'en' ? 'en-US' : 'zh-TW')

export function setLang(l: Lang) {
  if (l === current) return
  current = l
  try {
    localStorage.setItem(KEY, l)
  } catch {
    /* storage blocked: choice lasts for this page view */
  }
  apply(l)
  listeners.forEach((cb) => cb())
}

export function subscribeLang(cb: () => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}
