import { useEffect, useRef } from 'react'

type Handler = (e: KeyboardEvent) => void

/** Map of key spec → handler. Spec: 'space', 'j', 'shift+]', 'mod+enter', 'arrowleft', '?'.
 *  Ignored while typing in inputs unless the spec includes 'mod+' or allowInInputs. */
export function useHotkeys(map: Record<string, Handler>, opts: { enabled?: boolean; allowInInputs?: string[] } = {}) {
  const ref = useRef(map)
  ref.current = map
  const { enabled = true, allowInInputs = [] } = opts
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      const parts: string[] = []
      if (e.metaKey || e.ctrlKey) parts.push('mod')
      if (e.altKey) parts.push('alt')
      let key = e.key.toLowerCase()
      if (key === ' ') key = 'space'
      // shift is implicit for symbol keys like '?' and '{'
      if (e.shiftKey && key.length > 1) parts.push('shift')
      parts.push(key)
      const spec = parts.join('+')
      const h = ref.current[spec]
      if (!h) return
      if (typing && !spec.startsWith('mod+') && !allowInInputs.includes(spec)) return
      if (document.querySelector('[role="dialog"]') && !spec.startsWith('mod+') && spec !== 'escape') return
      e.preventDefault()
      h(e)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled, allowInInputs.join(',')])
}
