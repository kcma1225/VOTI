import { useSyncExternalStore } from 'react'

export function useMediaQuery(q: string) {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(q)
      m.addEventListener('change', cb)
      return () => m.removeEventListener('change', cb)
    },
    () => window.matchMedia(q).matches,
  )
}
export const useIsPhone = () => useMediaQuery('(max-width: 767.98px)')
export const useIsDesktop = () => useMediaQuery('(min-width: 1280px)')
