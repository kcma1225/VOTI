import { useState } from 'react'
import { useChannels } from '@/api/queries'
import { Page, PageHeader } from '@/components/layout/page'
import { Select } from '@/components/ui/form'
import { QualityPanels } from '@/components/domain/quality-viz'

export default function Quality() {
  const [channel, setChannel] = useState('')
  const ch = useChannels()
  return (
    <Page>
      <PageHeader
        title="音質總覽"
        description="已處理影片的音質分佈：SNR、DNSMOS、殘響、實際頻寬與語音比例，以及取樣率與編碼組成。"
        actions={
          <Select value={channel} onChange={(e) => setChannel(e.target.value)} className="w-full sm:w-56" aria-label="範圍">
            <option value="">所有頻道</option>
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
