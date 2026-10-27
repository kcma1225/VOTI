/* Review filters live in the URL query string, shared by the list (/review) and the reviewer (/review/:id), so
 * "back to list" restores the same filtered list and prev/next move within it. */
import { useSearchParams } from 'react-router'
import type { ReviewStatus, SegmentQuery } from '@/api/types'

export const LIST_LIMIT = 50

export interface ReviewParams {
  media: string
  channel: string
  status: ReviewStatus | 'all'
  source: string
  flags: string[]
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
  return { p, set, search: sp.toString(), sp }
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
