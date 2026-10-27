import { t } from '@/i18n'
import type { ReactNode } from 'react'
import { Dialog as D, DropdownMenu as DM, Popover as P, Tooltip as T } from 'radix-ui'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

/* ───────────── Dialog: centered on ≥ sm, bottom sheet on phones ───────────── */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
  trigger,
  size = 'md',
}: {
  open?: boolean
  onOpenChange?: (o: boolean) => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  className?: string
  trigger?: ReactNode
  size?: 'sm' | 'md' | 'lg'
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      {trigger && <D.Trigger asChild>{trigger}</D.Trigger>}
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-overlay backdrop-blur-[2px] data-[state=open]:animate-fade-in" />
        <D.Content
          className={cn(
            'fixed z-50 flex max-h-[92dvh] w-full flex-col overflow-hidden border border-border bg-surface shadow-lg outline-none',
            'inset-x-0 bottom-0 rounded-t-2xl data-[state=open]:animate-slide-up pb-safe',
            'sm:inset-x-auto sm:bottom-auto sm:left-1/2 sm:top-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-xl sm:pb-0',
            size === 'sm' && 'sm:max-w-md',
            size === 'md' && 'sm:max-w-lg',
            size === 'lg' && 'sm:max-w-2xl',
            className,
          )}
        >
          <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-border-strong sm:hidden" />
          <div className="flex items-start justify-between gap-4 px-5 pb-1 pt-4 sm:pt-5">
            <div className="min-w-0">
              <D.Title className="text-base font-semibold text-fg">{title}</D.Title>
              {description ? (
                <D.Description className="mt-1 text-[13px] leading-5 text-fg-3">{description}</D.Description>
              ) : (
                <D.Description className="sr-only">{typeof title === 'string' ? title : t('common.dialog')}</D.Description>
              )}
            </div>
            <D.Close className="-mr-2 -mt-1 grid size-10 shrink-0 place-items-center rounded-lg text-fg-3 hover:bg-surface-2 hover:text-fg sm:size-8" aria-label={t('common.close')}>
              <X className="size-4" />
            </D.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">{children}</div>
          {footer && (
            <div className="flex flex-col-reverse gap-2 border-t border-border bg-surface-2/50 px-5 py-3 sm:flex-row sm:justify-end">{footer}</div>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  )
}
export const DialogClose = D.Close

/* ───────────── Sheet: side panel (right) / drawer (left) ───────────── */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  side = 'right',
  children,
  className,
  headerActions,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  title: ReactNode
  description?: ReactNode
  side?: 'left' | 'right'
  children: ReactNode
  className?: string
  headerActions?: ReactNode
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-overlay data-[state=open]:animate-fade-in" />
        <D.Content
          className={cn(
            'fixed inset-y-0 z-50 flex w-full flex-col bg-surface shadow-lg outline-none',
            side === 'right'
              ? 'right-0 max-w-[min(100vw,440px)] border-l border-border data-[state=open]:animate-slide-in-right'
              : 'left-0 max-w-[min(86vw,300px)] border-r border-border data-[state=open]:animate-slide-in-left',
            className,
          )}
        >
          <div className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
            <div className="min-w-0">
              <D.Title className="truncate text-[15px] font-semibold">{title}</D.Title>
              {description ? (
                <D.Description className="truncate text-xs text-fg-3">{description}</D.Description>
              ) : (
                <D.Description className="sr-only">{typeof title === 'string' ? title : t('common.panel')}</D.Description>
              )}
            </div>
            <div className="flex items-center gap-1">
              {headerActions}
              <D.Close className="grid size-10 place-items-center rounded-lg text-fg-3 hover:bg-surface-2 hover:text-fg sm:size-9" aria-label={t('common.close')}>
                <X className="size-4" />
              </D.Close>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  )
}

/* ───────────── Dropdown menu ───────────── */
export const Menu = DM.Root
export const MenuTrigger = DM.Trigger
export function MenuContent({ children, align = 'end', className }: { children: ReactNode; align?: 'start' | 'end' | 'center'; className?: string }) {
  return (
    <DM.Portal>
      <DM.Content
        align={align}
        sideOffset={6}
        collisionPadding={8}
        className={cn(
          'z-50 min-w-48 max-w-[calc(100vw-16px)] overflow-hidden rounded-lg border border-border bg-surface p-1 shadow-lg data-[state=open]:animate-slide-up',
          className,
        )}
      >
        {children}
      </DM.Content>
    </DM.Portal>
  )
}
export function MenuItem({
  children,
  onSelect,
  icon,
  danger,
  disabled,
  shortcut,
}: {
  children: ReactNode
  onSelect?: () => void
  icon?: ReactNode
  danger?: boolean
  disabled?: boolean
  shortcut?: ReactNode
}) {
  return (
    <DM.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        'flex h-10 cursor-pointer select-none items-center gap-2.5 rounded-md px-2.5 text-sm outline-none sm:h-8 [&_svg]:size-4',
        danger ? 'text-danger-fg data-[highlighted]:bg-danger-soft' : 'text-fg data-[highlighted]:bg-surface-2',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
      )}
    >
      {icon && <span className={cn('shrink-0', danger ? '' : 'text-fg-3')}>{icon}</span>}
      <span className="flex-1 truncate">{children}</span>
      {shortcut && <span className="ml-4 text-xs text-fg-3">{shortcut}</span>}
    </DM.Item>
  )
}
export function MenuLabel({ children }: { children: ReactNode }) {
  return <DM.Label className="px-2.5 pb-1 pt-2 text-xs font-medium text-fg-3">{children}</DM.Label>
}
export function MenuSeparator() {
  return <DM.Separator className="-mx-1 my-1 h-px bg-border" />
}
export const MenuRadioGroup = DM.RadioGroup
export function MenuRadioItem({ value, children, icon }: { value: string; children: ReactNode; icon?: ReactNode }) {
  return (
    <DM.RadioItem
      value={value}
      className="flex h-10 cursor-pointer select-none items-center gap-2.5 rounded-md px-2.5 text-sm text-fg outline-none data-[highlighted]:bg-surface-2 data-[state=checked]:font-medium sm:h-8 [&_svg]:size-4"
    >
      {icon && <span className="text-fg-3">{icon}</span>}
      <span className="flex-1">{children}</span>
      <DM.ItemIndicator>
        <span className="block size-1.5 rounded-full bg-primary" />
      </DM.ItemIndicator>
    </DM.RadioItem>
  )
}

/* ───────────── Tooltip ───────────── */
export const TooltipProvider = T.Provider
export function Tooltip({ content, children, side = 'top' }: { content: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  if (!content) return <>{children}</>
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className="z-[60] max-w-72 rounded-md bg-fg px-2.5 py-1.5 text-xs leading-5 text-fg-inverse shadow-md data-[state=delayed-open]:animate-fade-in"
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  )
}

/* ───────────── Popover ───────────── */
export function Popover({
  trigger,
  children,
  align = 'start',
  className,
  open,
  onOpenChange,
}: {
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  className?: string
  open?: boolean
  onOpenChange?: (o: boolean) => void
}) {
  return (
    <P.Root open={open} onOpenChange={onOpenChange}>
      <P.Trigger asChild>{trigger}</P.Trigger>
      <P.Portal>
        <P.Content
          align={align}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            'z-50 w-80 max-w-[calc(100vw-16px)] rounded-xl border border-border bg-surface p-4 shadow-lg outline-none data-[state=open]:animate-slide-up',
            className,
          )}
        >
          {children}
        </P.Content>
      </P.Portal>
    </P.Root>
  )
}
