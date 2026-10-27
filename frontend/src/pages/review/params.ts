/* Review filters live in the URL query string, shared by the list (/review) and the reviewer (/review/:id), so
 * "back to list" restores the same filtered list and prev/next move within it. */
import { useSearchParams } from 'react-router'
import type { ReviewStatus, SegmentQuery, Validity } from '@/api/types'

export const LIST_LIMIT = 50

export interface ReviewParams {
  media: string
  channel: string
  status: ReviewStatus | 'all'
  source: string
  flags: string[]
  /** caption check: '' = all */
  validity: Validity | ''
  snr: number | null
  mos: number | null
  q: string
  sort: string
  offset: number
}

const numOrNull = (v: string | null) => (v === null || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

export function parseReviewParams(sp: URLSearchParams): ReviewParams {
  return {
    media: sp.get('media') ?? '',
    channel: sp.get('channel') ?? '',
    status: (sp.get('status') ?? 'unchecked') as ReviewStatus | 'all',
    source: sp.get('source') ?? '',
    flags: (sp.get('flags') ?? '').split(',').filter(Boolean),
    validity: (['valid', 'invalid', 'unknown'].includes(sp.get('validity') ?? '') ? sp.get('validity') : '') as Validity | '',
    snr: numOrNull(sp.get('snr')),
    mos: numOrNull(sp.get('mos')),
    q: sp.get('q') ?? '',
    sort: sp.get('sort') ?? '',
    offset: Number(sp.get('offset') ?? 0) || 0,
  }
}

/** API filters (no paging) for GET /segments and GET /segments/{id}/neighbors. */
export function toSegmentQuery(p: ReviewParams): SegmentQuery {
  return {
    media_id: p.media || undefined,
    channel_id: p.channel || undefined,
    review_status: p.status === 'all' ? undefined : [p.status],
    text_source: p.source ? [p.source] : undefined,
    flags: p.flags.length ? p.flags : undefined,
    validity: p.validity ? [p.validity] : undefined,
    min_snr: p.snr ?? undefined,
    min_dnsmos: p.mos ?? undefined,
    q: p.q || undefined,
    sort: p.sort || undefined,
  }
}

export function useReviewParams() {
  const [sp, setSp] = useSearchParams()
  const p = parseReviewParams(sp)
  /** Patch query params (null/'' deletes). Any filter change resets paging unless `offset` is in the patch. */
  const set = (patch: Record<string, string | number | null>) => {
    const n = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') n.delete(k)
      else n.set(k, String(v))
    }
    if (!('offset' in patch)) n.delete('offset')
    setSp(n, { replace: true })
  }
  /** Replace the whole query string (no history entry). */
  const setAll = (search: string) => setSp(new URLSearchParams(search), { replace: true })
  return { p, set, setAll, search: sp.toString(), sp }
}

/** Query string without paging (used as the scroll-memory key and for neighbors). */
export function filterKey(sp: URLSearchParams) {
  const n = new URLSearchParams(sp)
  n.delete('offset')
  n.sort()
  return n.toString()
}

const SCROLL_KEY = 'voti-review-scroll'
export function rememberListScroll(sp: URLSearchParams) {
  try {
    sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ k: sp.toString(), y: window.scrollY }))
  } catch {
    /* storage unavailable */
  }
}
export function recallListScroll(sp: URLSearchParams): number | null {
  try {
    const v = JSON.parse(sessionStorage.getItem(SCROLL_KEY) ?? 'null') as { k: string; y: number } | null
    return v && v.k === sp.toString() ? v.y : null
  } catch {
    return null
  }
}

/* ── reviewer ↔ list history ──
 * The list opens the reviewer with `state.fromList`; prev/next inside the reviewer replace the entry and carry the
 * state on. "Back to list" then goes back in history (one entry) instead of pushing a new list entry. The list it
 * returns to may show another page than the one the reviewer has moved on to, so the wanted list query and the
 * segment to highlight are handed over in sessionStorage; the list applies them with `replace`. */
export interface ReviewNavState {
  fromList?: boolean
  focus?: string
}
const RETURN_KEY = 'voti-review-return'
export function setListReturn(search: string, focus: string) {
  try {
    sessionStorage.setItem(RETURN_KEY, JSON.stringify({ search, focus }))
  } catch {
    /* storage unavailable */
  }
}
/** Read-and-clear the hand-over left by the reviewer (null when there is none). */
export function takeListReturn(): { search: string; focus: string } | null {
  try {
    const v = JSON.parse(sessionStorage.getItem(RETURN_KEY) ?? 'null') as { search: string; focus: string } | null
    sessionStorage.removeItem(RETURN_KEY)
    return v && typeof v.search === 'string' ? v : null
  } catch {
    return null
  }
}
