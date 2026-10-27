/* 島嶼之聲 / Voice of the Island logo — the original island outline + three sound-wave arcs
 * (same path geometry as public/brand/*.svg), drawn with `currentColor` so it adapts to the theme. */
import { cn } from '@/lib/cn'
import { t } from '@/i18n'

const ISLAND =
  'M140.4 16C144.6 16.8 151.6 23.9 156.1 27.2C160.6 30.5 167.1 32.1 167.5 35.8C167.9 39.4 159.9 44 158.5 48.9C157.1 53.9 161.2 56.6 159.1 65.4C157 74.2 149.6 89 145.8 101.6C142 114.3 140.9 127.5 136.2 141.2C131.4 154.9 123.2 171.9 117.5 184C111.7 196.1 104.9 204.3 101.8 213.6C98.7 223 100.5 236.2 98.8 240C97.1 243.8 94.3 242.2 91.5 236.7C88.7 231.2 86.5 214.7 81.9 207.1C77.2 199.4 68.8 198.3 63.8 190.6C58.7 182.9 52.4 172.5 51.7 160.9C51 149.4 55.7 132.9 59.6 121.4C63.4 109.9 68.6 102.2 74.6 91.8C80.7 81.3 89.2 68.7 95.7 58.8C102.3 48.9 108 38.5 113.8 32.5C119.7 26.4 126.3 25.3 130.7 22.6C135.2 19.8 136.2 15.2 140.4 16Z'
const ARCS = ['M155.7 108A30.3 30.3 0 0 1 155.7 145.3', 'M172.3 95A51.4 51.4 0 0 1 172.3 158.3', 'M188.9 82.1A72.5 72.5 0 0 1 188.9 171.3']

/**
 * variant="outline" — mark.svg/favicon.svg style (stroked island); good at small sizes.
 * variant="solid"   — logo.svg style (filled island, heavy arcs).
 * Color comes from `currentColor` (default: the brand token `text-brand`).
 */
export function Logo({
  className,
  variant = 'outline',
  strokeWidth,
  title,
}: {
  className?: string
  variant?: 'outline' | 'solid'
  strokeWidth?: number
  title?: string
}) {
  const label = title ?? t('common.brand.name')
  if (variant === 'solid')
    return (
      <svg viewBox="40 8 176 240" className={cn('text-brand', className)} role="img" aria-label={label}>
        <path d={ISLAND} fill="currentColor" />
        <g fill="none" stroke="currentColor" strokeWidth={strokeWidth ?? 20} strokeLinecap="round">
          {ARCS.map((d) => (
            <path key={d} d={d} />
          ))}
        </g>
      </svg>
    )
  return (
    <svg
      viewBox="40 8 176 240"
      className={cn('text-brand', className)}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth ?? 15}
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label={label}
    >
      <path d={ISLAND} />
      {ARCS.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  )
}

/** Logo on a soft brand-tinted tile, used in the sidebar, phone top bar and login. */
export function BrandMark({ className, size = 32 }: { className?: string; size?: number }) {
  return (
    <span
      className={cn('grid shrink-0 place-items-center rounded-lg bg-brand-soft ring-1 ring-inset ring-brand/15', className)}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <Logo className="h-[78%] w-auto" strokeWidth={size < 30 ? 17 : 15} />
    </span>
  )
}
