import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { Slot } from 'radix-ui'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'outline' | 'danger' | 'soft' | 'success' | 'warning'
type Size = 'xs' | 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm'

const variants: Record<Variant, string> = {
  primary: 'bg-primary text-primary-fg shadow-xs hover:bg-primary-hover active:translate-y-px',
  secondary:
    'bg-surface text-fg border border-border-strong/80 shadow-xs hover:bg-surface-2 hover:border-border-strong active:translate-y-px',
  outline: 'border border-border-strong text-fg hover:bg-surface-2',
  ghost: 'text-fg-2 hover:bg-surface-2 hover:text-fg',
  soft: 'bg-primary-soft text-primary-soft-fg hover:bg-primary-soft/70',
  danger: 'bg-danger text-white shadow-xs hover:brightness-95 active:translate-y-px',
  success: 'bg-success text-white shadow-xs hover:brightness-95 active:translate-y-px',
  warning: 'bg-warning text-white shadow-xs hover:brightness-95 active:translate-y-px',
}

// Mobile-first: every size is ≥ 40px tall below `sm`, denser on pointer devices.
const sizes: Record<Size, string> = {
  xs: 'h-8 sm:h-7 px-2.5 text-xs gap-1 rounded-md',
  sm: 'h-10 sm:h-8 px-3 text-[13px] gap-1.5 rounded-md',
  md: 'h-10 sm:h-9 px-3.5 text-sm gap-2 rounded-lg',
  lg: 'h-12 sm:h-11 px-5 text-[15px] gap-2 rounded-lg',
  icon: 'size-10 sm:size-9 rounded-lg',
  'icon-sm': 'size-10 sm:size-8 rounded-md',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'md', loading, asChild, disabled, children, ...props },
  ref,
) {
  const Comp = asChild ? Slot.Root : 'button'
  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : (props.type ?? 'button')}
      disabled={disabled || loading}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium transition-[background,color,border,box-shadow,transform] duration-150',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50',
        '[&_svg]:size-4 [&_svg]:shrink-0',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {asChild ? (
        children
      ) : (
        <>
          {loading && <Loader2 className="animate-spin" />}
          {children}
        </>
      )}
    </Comp>
  )
})
