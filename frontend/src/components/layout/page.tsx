import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ChevronLeft } from 'lucide-react'
import { cn } from '@/lib/cn'
import { t } from '@/i18n'

export function Page({ children, className, wide }: { children: ReactNode; className?: string; wide?: boolean }) {
  return (
    <main className={cn('mx-auto w-full min-w-0 px-4 py-5 sm:px-6 sm:py-7 lg:px-8', wide ? 'max-w-[1680px]' : 'max-w-[1440px]', className)}>
      {children}
    </main>
  )
}

export function useTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · ${t('common.brand.name')}` : t('common.brand.name')
  }, [title])
}

export function PageHeader({
  title,
  description,
  actions,
  back,
  eyebrow,
  className,
  children,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  back?: { to: string; label: string }
  eyebrow?: ReactNode
  className?: string
  children?: ReactNode
}) {
  useTitle(typeof title === 'string' ? title : undefined)
  return (
    <header className={cn('mb-5 sm:mb-6', className)}>
      {back && (
        <Link
          to={back.to}
          className="-ml-1 mb-2 inline-flex h-8 items-center gap-1 rounded-md pl-0.5 pr-2 text-[13px] text-fg-3 transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <ChevronLeft className="size-4" />
          {back.label}
        </Link>
      )}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
        <div className="min-w-0">
          {eyebrow && <div className="mb-1 text-[13px] text-fg-3">{eyebrow}</div>}
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight text-fg break-cjk sm:text-2xl">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-[13px] leading-5 text-fg-3 sm:text-sm">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  )
}
