import { useCallback, useEffect, useSyncExternalStore } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'
const KEY = 'voti-theme'
const listeners = new Set<() => void>()

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}
const mql = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null

function apply(pref: ThemePref) {
  const dark = pref === 'dark' || (pref === 'system' && !!mql?.matches)
  const d = document.documentElement
  d.classList.toggle('dark', dark)
  d.dataset.theme = dark ? 'dark' : 'light'
}

let current: ThemePref = typeof window !== 'undefined' ? readPref() : 'system'
mql?.addEventListener('change', () => {
  if (current === 'system') {
    apply('system')
    listeners.forEach((l) => l())
  }
})

export function useTheme() {
  const pref = useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => current,
  )
  const resolved = useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => (document.documentElement.classList.contains('dark') ? 'dark' : 'light'),
  )
  const setPref = useCallback((p: ThemePref) => {
    current = p
    try {
      if (p === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, p)
    } catch {
      /* ignore */
    }
    apply(p)
    listeners.forEach((l) => l())
  }, [])
  useEffect(() => apply(current), [])
  return { pref, resolved: resolved as 'light' | 'dark', setPref }
}
