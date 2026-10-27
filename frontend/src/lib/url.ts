export type UrlKind =
  | { kind: 'empty' }
  | { kind: 'invalid'; reason: string }
  | { kind: 'video'; id: string; short: boolean }
  | { kind: 'channel'; handle: string; tab: string | null }
  | { kind: 'playlist'; id: string; withVideo: boolean }

/** Instant client-side classification (mirrors POST /sources/resolve accepted forms). */
export function classifyUrl(raw: string): UrlKind {
  const s = raw.trim()
  if (!s) return { kind: 'empty' }
  let u: URL
  try {
    u = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`)
  } catch {
    return { kind: 'invalid', reason: '這不是有效的網址' }
  }
  const host = u.hostname.replace(/^(www\.|m\.|music\.)/, '')
  if (host === 'youtu.be') {
    const id = u.pathname.slice(1).split('/')[0]
    return id ? { kind: 'video', id, short: false } : { kind: 'invalid', reason: '缺少影片 ID' }
  }
  if (host !== 'youtube.com') return { kind: 'invalid', reason: '目前只支援 YouTube 網址' }
  const parts = u.pathname.split('/').filter(Boolean)
  const list = u.searchParams.get('list')
  const v = u.searchParams.get('v')
  if (parts[0] === 'playlist' && list) return { kind: 'playlist', id: list, withVideo: false }
  if (parts[0] === 'watch' && v) return list ? { kind: 'playlist', id: list, withVideo: true } : { kind: 'video', id: v, short: false }
  if (parts[0] === 'shorts' && parts[1]) return { kind: 'video', id: parts[1], short: true }
  if (parts[0]?.startsWith('@')) return { kind: 'channel', handle: decodeURIComponent(parts[0]), tab: parts[1] ?? null }
  if ((parts[0] === 'channel' || parts[0] === 'c' || parts[0] === 'user') && parts[1])
    return { kind: 'channel', handle: parts[0] === 'channel' ? parts[1] : `/${parts[0]}/${parts[1]}`, tab: parts[2] ?? null }
  return { kind: 'invalid', reason: '無法辨識這個 YouTube 網址的類型' }
}
