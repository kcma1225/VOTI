import { Link } from 'react-router'
import { Compass } from 'lucide-react'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/primitives'
import { EmptyState } from '@/components/ui/states'

export default function NotFound() {
  useTitle('找不到頁面')
  return (
    <Page>
      <Card className="mt-6">
        <EmptyState
          icon={<Compass />}
          title="找不到這個頁面"
          description="網址可能打錯了，或這個項目已被刪除。"
          action={
            <Button asChild variant="primary">
              <Link to="/">回到總覽</Link>
            </Button>
          }
        />
      </Card>
    </Page>
  )
}
