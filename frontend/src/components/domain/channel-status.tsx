import { AlertTriangle, Clock, Loader2, Sparkles } from 'lucide-react'
import type { ChannelSummary } from '@/api/types'
import { useT } from '@/i18n'
import { Badge } from '@/components/ui/primitives'

/** queued / listing / failed badge for a channel's listing state (nothing once listed). */
export function ListStatusBadge({ c, className }: { c: ChannelSummary; className?: string }) {
  const t = useT()
  if (c.list_status === 'queued' || c.list_status === 'new')
    return (
      <Badge tone="info" className={className}>
        <Clock className="size-3" /> {t('collect.channels.queued')}
      </Badge>
    )
  if (c.list_status === 'listing')
    return (
      <Badge tone="primary" className={className}>
        <Loader2 className="size-3 animate-spin" /> {t('collect.channels.listing')}
      </Badge>
    )
  if (c.list_status === 'failed')
    return (
      <Badge tone="danger" className={className}>
        <AlertTriangle className="size-3" /> {t('collect.channels.listFailed')}
      </Badge>
    )
  return null
}

export function NewVideosBadge({ n, className }: { n?: number; className?: string }) {
  const t = useT()
  if (!n) return null
  return (
    <Badge tone="primary" className={className}>
      <Sparkles className="size-3" /> {t('collect.channels.newVideos', n)}
    </Badge>
  )
}
