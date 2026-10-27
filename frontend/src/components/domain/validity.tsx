/* Caption ↔ audio validation: invalid-segment badge, manual override actions, media summary chip. */
import { toast } from 'sonner'
import { ShieldAlert, ShieldCheck, ShieldX, Undo2, UserCheck } from 'lucide-react'
import { msgs, t } from '@/i18n'
import type { SegmentDetail, SegmentSummary } from '@/api/types'
import { useSegmentPatch } from '@/api/queries'
import { lang } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { Badge } from '@/components/ui/primitives'
import { Tooltip } from '@/components/ui/overlay'
import { Button } from '@/components/ui/button'

type Seg = Pick<SegmentSummary, 'id' | 'validity' | 'invalid_reason' | 'validity_source' | 'lid_lang' | 'lid_prob' | 'align_score'> & {
  validation?: SegmentDetail['validation']
}

/** VoxLingua107 code → language name in the active UI language (falls back to caption labels, then the code). */
export const lidLangName = (code: string | null | undefined): string | null => {
  if (!code) return null
  return msgs().validate.lang[code] ?? lang(code)
}

export const isInvalid = (s: Pick<SegmentSummary, 'validity'>) => s.validity === 'invalid'

/** Full sentence for an invalid segment, e.g. 「無效：語言不符（音訊為英文 92%）」. */
export function invalidText(s: Seg): string {
  if (s.invalid_reason === 'language_mismatch') return t('validate.reasonLang', lidLangName(s.lid_lang), s.lid_prob ?? null)
  if (s.invalid_reason === 'text_mismatch') return t('validate.reasonText')
  if (s.invalid_reason === 'manual' || s.validity_source === 'manual') return t('validate.reasonManual')
  return t('validate.invalidShort')
}

function Details({ s }: { s: Seg }) {
  const lid = s.validation?.lid
  return (
    <div className="max-w-72 space-y-1 text-xs leading-5">
      <div className="font-medium">{t('validate.tip.title')}</div>
      <div>
        {t('validate.tip.lid')}：
        {lid?.length
          ? lid.map(([c, p]) => `${lidLangName(c)} ${Math.round(p * 100)}%`).join(' · ')
          : s.lid_lang
            ? `${lidLangName(s.lid_lang)} ${s.lid_prob != null ? Math.round(s.lid_prob * 100) + '%' : ''}`
            : t('validate.tip.none')}
      </div>
      <div>
        {t('validate.tip.align')}：{s.align_score != null ? s.align_score.toFixed(2) : t('validate.tip.none')}
      </div>
      {s.validity === 'invalid' && <div className="opacity-80">{t('validate.tip.excluded')}</div>}
      {s.validity_source === 'manual' && <div className="opacity-80">{t('validate.tip.manualNote')}</div>}
    </div>
  )
}

/** Invalid badge with reason (wraps on narrow screens); a small "manual" mark for human-validated segments. */
export function ValidityBadge({ s, short, className }: { s: Seg; short?: boolean; className?: string }) {
  const manual = s.validity_source === 'manual'
  if (s.validity === 'invalid') {
    const label = short
      ? s.invalid_reason === 'language_mismatch'
        ? t('validate.reasonLangShort')
        : s.invalid_reason === 'text_mismatch'
          ? t('validate.reasonTextShort')
          : t('validate.invalidShort')
      : invalidText(s)
    return (
      <Tooltip content={<Details s={s} />}>
        <Badge
          tone="danger"
          data-validity="invalid"
          className={cn('h-auto min-h-[22px] whitespace-normal py-0.5 text-left leading-4 break-cjk', className)}
        >
          <ShieldX className="size-3 shrink-0" />
          <span className="min-w-0">{label}</span>
          {manual && !short && s.invalid_reason !== 'manual' && <span className="opacity-75">· {t('validate.manual')}</span>}
        </Badge>
      </Tooltip>
    )
  }
  if (s.validity === 'valid' && manual)
    return (
      <Tooltip content={<Details s={s} />}>
        <Badge tone="success" className={className}>
          <UserCheck className="size-3" /> {t('validate.manualValid')}
        </Badge>
      </Tooltip>
    )
  return null
}

/** Mark valid / invalid (and back to automatic when the current state is a manual decision). */
export function useValidityActions(s: Seg) {
  const patch = useSegmentPatch()
  const set = (v: 'valid' | 'invalid' | 'auto') =>
    patch.mutate(
      { id: s.id, patch: { validity: v } },
      { onSuccess: () => toast.success(v === 'valid' ? t('validate.markedValid') : v === 'invalid' ? t('validate.markedInvalid') : t('validate.backToAuto'), { duration: 1600 }) },
    )
  const invalid = s.validity === 'invalid'
  return {
    pending: patch.isPending,
    invalid,
    manual: s.validity_source === 'manual',
    toggle: () => set(invalid ? 'valid' : 'invalid'),
    auto: () => set('auto'),
    toggleLabel: invalid ? t('validate.markValid') : t('validate.markInvalid'),
    ToggleIcon: invalid ? ShieldCheck : ShieldAlert,
  }
}

/** Buttons for the reviewer panel. */
export function ValidityControls({ s, className }: { s: Seg; className?: string }) {
  const a = useValidityActions(s)
  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      <Button size="sm" variant={a.invalid ? 'soft' : 'outline'} onClick={a.toggle} loading={a.pending}>
        <a.ToggleIcon /> {a.toggleLabel}
      </Button>
      {a.manual && (
        <Button size="sm" variant="ghost" onClick={a.auto} disabled={a.pending}>
          <Undo2 /> {t('validate.useAuto')}
        </Button>
      )}
    </div>
  )
}
