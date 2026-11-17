import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react'
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { forwardRef, useId } from 'react'

export function cx(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-700 text-white hover:bg-brand-800 shadow-sm disabled:bg-brand-300',
  secondary: 'bg-white text-brand-800 border border-brand-200 hover:bg-brand-50 hover:border-brand-300 disabled:text-slate-400',
  ghost: 'text-brand-800 hover:bg-brand-50 disabled:text-slate-400',
  danger: 'bg-white text-rose-700 border border-rose-200 hover:bg-rose-50 disabled:text-slate-400',
  success: 'bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-emerald-300',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: 'md' | 'lg' | 'sm'
  icon?: ReactNode
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', icon, loading, className, children, disabled, ...rest },
  ref,
) {
  const sizes = { sm: 'min-h-9 px-3 text-sm gap-1.5', md: 'min-h-11 px-4 gap-2', lg: 'min-h-14 px-6 text-lg gap-2.5' }
  return (
    <button
      ref={ref}
      className={cx(
        'inline-flex items-center justify-center rounded-xl font-bold transition-colors disabled:cursor-not-allowed',
        sizes[size],
        VARIANTS[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 className="size-5 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  )
})

export function IconButton({
  label,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cx(
        'inline-flex size-10 items-center justify-center rounded-lg text-slate-600 hover:bg-brand-50 hover:text-brand-800 disabled:opacity-40',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Card({ children, className, as: As = 'section' }: { children: ReactNode; className?: string; as?: 'section' | 'div' | 'article' }) {
  return <As className={cx('rounded-2xl border border-slate-200 bg-white shadow-card', className)}>{children}</As>
}

interface FieldProps {
  label: string
  hint?: string
  error?: string
  required?: boolean
  children: (id: string, describedBy: string | undefined) => ReactNode
  className?: string
  id?: string
}

export function Field({ label, hint, error, required, children, className, id: fixedId }: FieldProps) {
  const generated = useId()
  const id = fixedId ?? generated
  const hintId = hint || error ? `${id}-hint` : undefined
  return (
    <div className={cx('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className="font-bold text-slate-800">
        {label}
        {required && <span className="text-rose-600" aria-hidden> *</span>}
        {required && <span className="sr-only"> (obrigatório)</span>}
      </label>
      {children(id, hintId)}
      {(hint || error) && (
        <p id={hintId} className={cx('text-sm', error ? 'font-bold text-rose-700' : 'text-slate-600')}>
          {error ?? hint}
        </p>
      )}
    </div>
  )
}

/** Input look without a width, for fields that set their own size. */
export const inputBase =
  'min-h-11 rounded-xl border border-slate-300 bg-white px-3 text-ink placeholder:text-slate-400 hover:border-brand-300 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-100 aria-[invalid=true]:border-rose-500'
export const inputClass = `${inputBase} w-full`

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cx(inputClass, className)} {...rest} />
})

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(inputClass, 'pr-8', className)} {...rest}>
      {children}
    </select>
  )
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  label: string
  description?: string
}) {
  const id = useId()
  return (
    <div className="flex items-start gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx(
          'relative mt-0.5 inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors',
          checked ? 'bg-brand-700' : 'bg-slate-300',
        )}
      >
        <span
          className={cx('inline-block size-5 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-6' : 'translate-x-1')}
        />
      </button>
      <label htmlFor={id} className="cursor-pointer">
        <span className="font-bold text-slate-800">{label}</span>
        {description && <span className="block text-sm text-slate-600">{description}</span>}
      </label>
    </div>
  )
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap gap-1 rounded-xl bg-slate-100 p-1">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            'min-h-10 rounded-lg px-3 text-sm font-bold transition-colors',
            value === o.value ? 'bg-white text-brand-800 shadow-sm' : 'text-slate-600 hover:text-brand-800',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

const ALERT_STYLES = {
  info: { box: 'border-brand-200 bg-brand-50 text-brand-950', icon: <Info className="size-5 text-brand-700" aria-hidden /> },
  success: { box: 'border-emerald-200 bg-emerald-50 text-emerald-950', icon: <CheckCircle2 className="size-5 text-emerald-700" aria-hidden /> },
  warning: { box: 'border-amber-300 bg-amber-50 text-amber-950', icon: <AlertTriangle className="size-5 text-amber-700" aria-hidden /> },
  error: { box: 'border-rose-300 bg-rose-50 text-rose-950', icon: <XCircle className="size-5 text-rose-700" aria-hidden /> },
}

export function Alert({
  tone = 'info',
  title,
  children,
  action,
  className,
}: {
  tone?: keyof typeof ALERT_STYLES
  title?: string
  children?: ReactNode
  action?: ReactNode
  className?: string
}) {
  const style = ALERT_STYLES[tone]
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cx('flex gap-3 rounded-xl border p-4', style.box, className)}>
      <div className="mt-0.5 shrink-0">{style.icon}</div>
      <div className="min-w-0 flex-1">
        {title && <p className="font-bold">{title}</p>}
        {children && <div className="text-[0.95rem] leading-relaxed">{children}</div>}
        {action && <div className="mt-3 flex flex-wrap gap-2">{action}</div>}
      </div>
    </div>
  )
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-slate-600" role="status">
      <Loader2 className="size-5 animate-spin" aria-hidden />
      {label}
    </span>
  )
}

export function Badge({ tone, children, icon }: { tone: 'rose' | 'amber' | 'violet' | 'teal' | 'slate' | 'emerald' | 'brand'; children: ReactNode; icon?: ReactNode }) {
  const tones = {
    rose: 'bg-rose-100 text-rose-800',
    amber: 'bg-amber-100 text-amber-900',
    violet: 'bg-violet-100 text-violet-800',
    teal: 'bg-teal-100 text-teal-800',
    slate: 'bg-slate-100 text-slate-700',
    emerald: 'bg-emerald-100 text-emerald-800',
    brand: 'bg-brand-100 text-brand-800',
  }
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-sm font-bold', tones[tone])}>
      {icon}
      {children}
    </span>
  )
}
