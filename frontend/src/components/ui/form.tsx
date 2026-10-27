import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { Checkbox as CheckboxP, Slider as SliderP, Switch as SwitchP, ToggleGroup } from 'radix-ui'
import { Check, ChevronDown, Minus, Search, X } from 'lucide-react'
import { cn } from '@/lib/cn'

const fieldBase =
  'w-full min-w-0 rounded-lg border border-border-strong/80 bg-surface text-fg shadow-xs outline-none transition-[border,box-shadow] placeholder:text-fg-3 hover:border-border-strong focus:border-primary focus:ring-3 focus:ring-ring/25 disabled:opacity-60 aria-[invalid=true]:border-danger'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function Input({ className, invalid, ...props }, ref) {
    return <input ref={ref} aria-invalid={invalid || undefined} className={cn(fieldBase, 'h-10 px-3 text-sm sm:h-9', className)} {...props} />
  },
)

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(fieldBase, 'min-h-20 px-3 py-2 text-sm leading-relaxed', className)} {...props} />
})

export function SearchInput({
  value,
  onChange,
  placeholder = '搜尋',
  className,
  autoFocus,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  className?: string
  autoFocus?: boolean
}) {
  return (
    <div className={cn('relative min-w-0', className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-3" />
      <input
        type="search"
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(fieldBase, 'h-10 pl-9 pr-9 text-sm sm:h-9 [&::-webkit-search-cancel-button]:hidden')}
      />
      {value && (
        <button
          type="button"
          aria-label="清除搜尋"
          onClick={() => onChange('')}
          className="absolute right-1 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-md text-fg-3 hover:bg-surface-2 hover:text-fg sm:size-7"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

/** Native select, styled — best ergonomics on phones. */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { size_?: 'sm' | 'md' }>(
  function Select({ className, children, size_ = 'md', ...props }, ref) {
    return (
      <div className={cn('relative min-w-0', className)}>
        <select
          ref={ref}
          className={cn(
            fieldBase,
            'appearance-none truncate pl-3 pr-8 text-sm',
            size_ === 'sm' ? 'h-10 sm:h-8 text-[13px]' : 'h-10 sm:h-9',
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-fg-3" />
      </div>
    )
  },
)

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
  aside,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  children: ReactNode
  className?: string
  htmlFor?: string
  aside?: ReactNode
}) {
  return (
    <div className={cn('min-w-0 space-y-1.5', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[13px] font-medium text-fg">
          {label}
        </label>
        {aside && <span className="text-xs text-fg-3 tabular">{aside}</span>}
      </div>
      {children}
      {error ? <p className="text-xs text-danger-fg">{error}</p> : hint ? <p className="text-xs leading-5 text-fg-3">{hint}</p> : null}
    </div>
  )
}

export function Checkbox({
  checked,
  onCheckedChange,
  label,
  description,
  disabled,
  className,
  'aria-label': ariaLabel,
}: {
  checked: boolean | 'indeterminate'
  onCheckedChange: (v: boolean) => void
  label?: ReactNode
  description?: ReactNode
  disabled?: boolean
  className?: string
  'aria-label'?: string
}) {
  const id = useId()
  const box = (
    <CheckboxP.Root
      id={id}
      checked={checked}
      disabled={disabled}
      aria-label={ariaLabel}
      onCheckedChange={(v) => onCheckedChange(v === true)}
      className={cn(
        'peer grid size-[18px] shrink-0 place-items-center rounded-[5px] border border-border-strong bg-surface shadow-xs transition-colors',
        'hover:border-fg-3 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50',
      )}
    >
      <CheckboxP.Indicator className="text-primary-fg">
        {checked === 'indeterminate' ? <Minus className="size-3" strokeWidth={3} /> : <Check className="size-3" strokeWidth={3} />}
      </CheckboxP.Indicator>
    </CheckboxP.Root>
  )
  if (!label) return <span className={cn('inline-grid place-items-center', className)}>{box}</span>
  return (
    <label htmlFor={id} className={cn('flex min-h-10 cursor-pointer items-start gap-2.5 py-1 sm:min-h-0', className)}>
      <span className="mt-[3px]">{box}</span>
      <span className="min-w-0">
        <span className="block text-sm leading-6 text-fg">{label}</span>
        {description && <span className="block text-xs leading-5 text-fg-3">{description}</span>}
      </span>
    </label>
  )
}

export function Switch({
  checked,
  onCheckedChange,
  label,
  description,
  disabled,
  className,
}: {
  checked: boolean
  onCheckedChange: (v: boolean) => void
  label?: ReactNode
  description?: ReactNode
  disabled?: boolean
  className?: string
}) {
  const id = useId()
  const sw = (
    <SwitchP.Root
      id={id}
      checked={checked}
      disabled={disabled}
      onCheckedChange={onCheckedChange}
      className={cn(
        'relative inline-flex h-6 w-10 shrink-0 items-center rounded-full border border-transparent bg-surface-3 transition-colors data-[state=checked]:bg-primary',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50',
      )}
    >
      <SwitchP.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-[18px]" />
    </SwitchP.Root>
  )
  if (!label) return sw
  return (
    <div className={cn('flex min-h-10 items-start justify-between gap-4', className)}>
      <label htmlFor={id} className="min-w-0 cursor-pointer">
        <span className="block text-sm font-medium leading-6 text-fg">{label}</span>
        {description && <span className="block text-xs leading-5 text-fg-3">{description}</span>}
      </label>
      <div className="pt-0.5">{sw}</div>
    </div>
  )
}

export function Slider({
  value,
  onValueChange,
  min = 0,
  max = 1,
  step = 0.01,
  className,
  'aria-label': ariaLabel,
}: {
  value: number[]
  onValueChange: (v: number[]) => void
  min?: number
  max?: number
  step?: number
  className?: string
  'aria-label'?: string
}) {
  return (
    <SliderP.Root
      value={value}
      onValueChange={onValueChange}
      min={min}
      max={max}
      step={step}
      className={cn('relative flex h-10 w-full touch-none select-none items-center sm:h-6', className)}
    >
      <SliderP.Track className="relative h-1.5 grow overflow-hidden rounded-full bg-surface-3">
        <SliderP.Range className="absolute h-full bg-primary" />
      </SliderP.Track>
      {value.map((_, i) => (
        <SliderP.Thumb
          key={i}
          aria-label={ariaLabel}
          className="block size-5 rounded-full border-2 border-primary bg-surface shadow-sm transition-shadow focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/30"
        />
      ))}
    </SliderP.Root>
  )
}

/** Segmented control (single choice). */
export function Segmented<V extends string>({
  value,
  onChange,
  options,
  className,
  size = 'md',
  'aria-label': ariaLabel,
}: {
  value: V
  onChange: (v: V) => void
  options: { value: V; label: ReactNode; count?: number }[]
  className?: string
  size?: 'sm' | 'md'
  'aria-label'?: string
}) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      aria-label={ariaLabel}
      onValueChange={(v) => v && onChange(v as V)}
      className={cn('inline-flex min-w-0 max-w-full items-center gap-0.5 overflow-x-auto rounded-lg bg-surface-2 p-0.5 no-scrollbar', className)}
    >
      {options.map((o) => (
        <ToggleGroup.Item
          key={o.value}
          value={o.value}
          className={cn(
            'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 font-medium text-fg-2 transition-colors hover:text-fg',
            'data-[state=on]:bg-surface data-[state=on]:text-fg data-[state=on]:shadow-sm',
            'focus-visible:outline-2 focus-visible:outline-ring',
            size === 'sm' ? 'h-9 text-[13px] sm:h-7' : 'h-9 text-sm sm:h-8',
          )}
        >
          {o.label}
          {o.count !== undefined && <span className="text-xs tabular text-fg-3">{o.count.toLocaleString('zh-TW')}</span>}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  )
}

/** Toggleable filter chip (multi-select facet). */
export function Chip({
  active,
  onClick,
  children,
  count,
  className,
  disabled,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
  count?: number
  className?: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 text-[13px] font-medium transition-colors sm:h-8',
        active
          ? 'border-primary/40 bg-primary-soft text-primary-soft-fg'
          : 'border-border-strong/70 bg-surface text-fg-2 hover:border-border-strong hover:text-fg',
        'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:opacity-50',
        className,
      )}
    >
      {active && <Check className="-ml-0.5 size-3.5" />}
      {children}
      {count !== undefined && (
        <span className={cn('tabular text-xs', active ? 'text-primary-soft-fg/80' : 'text-fg-3')}>{count.toLocaleString('zh-TW')}</span>
      )}
    </button>
  )
}

export function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  suffix,
  className,
  placeholder,
  'aria-label': ariaLabel,
}: {
  value: number | null
  onChange: (v: number | null) => void
  min?: number
  max?: number
  step?: number
  suffix?: string
  className?: string
  placeholder?: string
  'aria-label'?: string
}) {
  return (
    <div className={cn('relative min-w-0', className)}>
      <input
        type="number"
        inputMode="decimal"
        aria-label={ariaLabel}
        value={value ?? ''}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder ?? '不限'}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        className={cn(fieldBase, 'h-10 px-3 text-sm tabular sm:h-9', suffix && 'pr-12')}
      />
      {suffix && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-fg-3">{suffix}</span>}
    </div>
  )
}
