import { useT } from '@/i18n'
import { useState } from 'react'
import { useChannels } from '@/api/queries'
import { Page, PageHeader } from '@/components/layout/page'
import { Select } from '@/components/ui/form'
import { QualityPanels } from '@/components/domain/quality-viz'

export default function Quality() {
  const t = useT()
  const [channel, setChannel] = useState('')
  const ch = useChannels()
  return (
    <Page>
      <PageHeader
        title={t('process.quality.title')}
        description={t('process.quality.description')}
        actions={
          <Select value={channel} onChange={(e) => setChannel(e.target.value)} className="w-full sm:w-56" aria-label={t('process.quality.scope')}>
            <option value="">{t('process.quality.allChannels')}</option>
            {ch.data?.items.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        }
      />
      <QualityPanels channelId={channel || undefined} />
    </Page>
  )
}
