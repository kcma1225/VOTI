/* Delete videos completely (admin): impact preview, explicit "cannot be undone" consent when anything was
   downloaded or reviewed. Used by the media detail page and the batch bar of the media lists. */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Trash2, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, onErr } from '@/api/queries'
import type { MediaQuery } from '@/api/types'
import { fmtBytes, fmtHours, fmtInt } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/primitives'
import { Checkbox } from '@/components/ui/form'
import { Dialog } from '@/components/ui/overlay'
import { useT } from '@/i18n'

export type MediaSelection = { ids?: string[]; filter?: MediaQuery }

export function DeleteMediaDialog({
  open,
  onOpenChange,
  selection,
  count,
  title,
  onDone,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  selection: MediaSelection
  count: number
  /** the single video's title (detail page) */
  title?: string
  onDone?: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const [agreed, setAgreed] = useState(false)
  const body = { media_ids: selection.ids, filter: selection.filter }
  const impact = useQuery({
    queryKey: ['media-delete-impact', selection.ids ?? null, selection.filter ?? null],
    queryFn: () => api.mediaDeleteImpact(body),
    enabled: open,
    staleTime: 0,
  })
  const d = impact.data
  const mustAgree = !!d?.requires_confirmation
  const del = useMutation({
    mutationFn: () => api.deleteMedia({ ...body, confirm: mustAgree }),
    onSuccess: (r) => {
      toast.success(t('remove.toastDone', r.media_total))
      onOpenChange(false)
      onDone?.()
      qc.invalidateQueries()
    },
    onError: onErr,
  })
  const n = d?.media_total ?? count
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o)
        if (o) setAgreed(false)
      }}
      title={t('remove.title', n)}
      description={title ? t('remove.descOne', title) : t('remove.descMany', n)}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button variant="danger" data-shot="confirm-delete-media" loading={del.isPending} disabled={!d || del.isPending || (mustAgree && !agreed) || n === 0} onClick={() => del.mutate()}>
            <Trash2 /> {t('remove.submit')}
          </Button>
        </>
      }
    >
      {impact.isLoading || !d ? (
        impact.isError ? <p className="text-[13px] text-danger-fg">{errMsg(impact.error)}</p> : <Skeleton className="h-24" />
      ) : (
        <div className="space-y-4">
          {mustAgree ? (
            <div className="flex gap-3 rounded-lg border border-danger/30 bg-danger-soft px-3.5 py-3 text-[13px] leading-5">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger-fg" />
              <div className="min-w-0 space-y-1.5">
                <p className="font-medium text-danger-fg">{t('remove.warnHead')}</p>
                <ul className="list-disc space-y-0.5 pl-4 text-fg-2">
                  {d.downloaded_media > 0 && <li>{t('remove.audio', d.downloaded_media, fmtHours(d.downloaded_sec), fmtBytes(d.storage_bytes))}</li>}
                  <li>{t('remove.analyses')}</li>
                  {d.segments > 0 && (
                    <li>
                      {t('remove.segments', d.segments)}
                      {d.reviewed_segments > 0 && (
                        <>
                          {t('remove.reviewedPart')}
                          <span className="font-medium text-danger-fg">{t('remove.reviewed', d.reviewed_segments)}</span>
                        </>
                      )}
                    </li>
                  )}
                  {d.active_jobs > 0 && <li>{t('remove.activeJobs', d.active_jobs)}</li>}
                </ul>
              </div>
            </div>
          ) : (
            <p className="text-[13px] leading-5 text-fg-2">{t('remove.metadataOnly')}</p>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-lg bg-surface-2 px-3.5 py-3 text-[13px] sm:grid-cols-4">
            {[
              [t('remove.statVideos'), fmtInt(d.media_total)],
              [t('remove.statDownloaded'), fmtInt(d.downloaded_media)],
              [t('remove.statSegments'), fmtInt(d.segments)],
              [t('remove.statStorage'), fmtBytes(d.storage_bytes)],
            ].map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-fg-3">{k}</dt>
                <dd className="tabular font-medium">{v}</dd>
              </div>
            ))}
          </dl>
          {mustAgree && <Checkbox checked={agreed} onCheckedChange={setAgreed} label={t('remove.understand')} />}
        </div>
      )}
    </Dialog>
  )
}
