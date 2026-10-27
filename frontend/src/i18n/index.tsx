/* Typed in-repo dictionary i18n.
 *
 *   const t = useT()
 *   t('common.cancel')                 // plain string leaf
 *   t('collect.videoCount', 12)        // function leaf: typed args, handles plurals/units per language
 *
 * Every namespace lives in ./messages/<ns>.ts(x) as `zh` (source of truth for the key set) and
 * `en: Shape<typeof zh>`, so a missing or extra English key fails `tsc`. Keys passed to t() are
 * checked against the zh tree, and function leaves check their argument types. */
import { Fragment, useSyncExternalStore, type ReactNode } from 'react'
import { getLang, setLang, subscribeLang, type Lang } from './lang'
import { zh, en } from './dict'

export { getLang, setLang, intlLocale, LANGS, type Lang } from './lang'
export type { Shape } from './shape'

export type Messages = typeof zh

type Leaf = string | ((...a: never[]) => unknown)
type Paths<T, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends Leaf ? `${P}${K}` : Paths<T[K], `${P}${K}.`>
}[keyof T & string]
export type MsgKey = Paths<Messages>
type Get<T, K extends string> = K extends `${infer H}.${infer R}`
  ? H extends keyof T
    ? Get<T[H], R>
    : never
  : K extends keyof T
    ? T[K]
    : never
type Args<V> = V extends (...a: infer A) => unknown ? A : []
type Ret<V> = V extends (...a: never[]) => infer R ? R : string

const dicts: Record<Lang, Messages> = { 'zh-TW': zh, en: en as Messages }

/** The whole message tree for the active language (for label maps that are looked up by data value). */
export const msgs = (): Messages => dicts[getLang()]

function lookup(tree: unknown, key: string): unknown {
  let v: unknown = tree
  for (const part of key.split('.')) {
    if (v == null || typeof v !== 'object') return undefined
    v = (v as Record<string, unknown>)[part]
  }
  return v
}

/** Translate a key in the active language. Usable outside React (toasts, label maps, formatters). */
export function t<K extends MsgKey>(key: K, ...args: Args<Get<Messages, K>>): Ret<Get<Messages, K>> {
  let v = lookup(msgs(), key)
  if (v === undefined) v = lookup(zh, key)
  if (typeof v === 'function') return (v as (...a: unknown[]) => Ret<Get<Messages, K>>)(...args)
  return (v ?? key) as Ret<Get<Messages, K>>
}

export function useLang(): Lang {
  return useSyncExternalStore(subscribeLang, getLang, getLang)
}

/** Subscribes the component to language changes and returns `t`. */
export function useT() {
  useLang()
  return t
}

export function useI18n() {
  const lang = useLang()
  return { lang, setLang, t }
}

/** Re-mounts the UI when the language changes so every label map / formatter re-evaluates
 * (react-query cache lives above this, so no data is refetched). */
export function I18nBoundary({ children }: { children: ReactNode }) {
  const lang = useLang()
  return <Fragment key={lang}>{children}</Fragment>
}

/** Language-aware lazy view of a record: every property read re-runs `build()` for the active
 * language, so module-level label maps keep their `map[key]` / Object.entries call sites. */
export function localized<T extends object>(build: () => T): T {
  return new Proxy({} as T, {
    get: (_t, p) => Reflect.get(build(), p),
    has: (_t, p) => Reflect.has(build(), p),
    ownKeys: () => Reflect.ownKeys(build()),
    getOwnPropertyDescriptor: (_t, p) => {
      const d = Reflect.getOwnPropertyDescriptor(build(), p)
      return d ? { ...d, configurable: true } : undefined
    },
  })
}
