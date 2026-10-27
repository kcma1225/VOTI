import { useT } from '@/i18n'
import { Link } from 'react-router'
import { Compass } from 'lucide-react'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/primitives'
import { EmptyState } from '@/components/ui/states'

export default function NotFound() {
  const t = useT()
  useTitle(t('shell.notFound.title'))
  return (
    <Page>
      <Card className="mt-6">
        <EmptyState
          icon={<Compass />}
          title={t('shell.notFound.heading')}
          description={t('shell.notFound.desc')}
          action={
            <Button asChild variant="primary">
              <Link to="/">{t('shell.notFound.home')}</Link>
            </Button>
          }
        />
      </Card>
    </Page>
  )
}
