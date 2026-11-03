/* "Back" that really goes back.
 *
 * In-page back controls (‹ channel, ‹ speakers, ‹ datasets, the reviewer's "back to list") return to the previous
 * in-app history entry when there is one, so one press of the control = one press of the browser Back button.
 * Without in-app history (deep link, new tab, reload of the first entry) they go to the page's logical parent and
 * *replace* the current entry, so a following Back never bounces into the page that was just left.
 *
 * In-app history depth comes from the data router's own marker: react-router stores `idx` in `history.state`
 * (0 for the first entry of the app, +1 per push, unchanged by replace), which also survives reloads. URL state
 * writes (tabs, filters, sort, paging, search, zoom) all use `replace`, so they never add entries. */
import { useCallback, type MouseEvent } from 'react'
import { useNavigate } from 'react-router'

/** true when the previous history entry belongs to this app */
export function canGoBack(): boolean {
  const idx = (window.history.state as { idx?: unknown } | null)?.idx
  return typeof idx === 'number' && idx > 0
}

export function useBack(fallback: string) {
  const navigate = useNavigate()
  return useCallback(() => {
    if (canGoBack()) navigate(-1)
    else navigate(fallback, { replace: true })
  }, [navigate, fallback])
}

/** onClick for a <Link to={fallback} replace>: a plain click goes back in history when possible; modified clicks
 * (new tab / window) keep the link's normal behaviour. */
export function useBackClick() {
  const navigate = useNavigate()
  return useCallback(
    (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
      if (!canGoBack()) return
      e.preventDefault()
      navigate(-1)
    },
    [navigate],
  )
}
