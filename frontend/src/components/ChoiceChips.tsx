import { Check, PenLine } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { PERIODOS } from '../lib/labels'
import type { Periodo } from '../api/types'
import { cx, inputClass } from './ui'

export interface ChoiceOption {
  value: string
  /** Text on the chip; defaults to the value. */
  label?: string
  /** What a screen reader says when the chip text is abbreviated ("1º" → "1º ano"). */
  ariaLabel?: string
}

interface ChoiceChipsProps {
  label: string
  value: string
  options: (string | ChoiceOption)[]
  onChange: (value: string) => void
  required?: boolean
  error?: string
  hint?: string
  /** Text of the chip that opens a field for any other value ("Outra"). Leave out to offer only the chips. */
  other?: string
  otherPlaceholder?: string
  otherMaxLength?: number
  /** "blur": the typed value is only sent when the field loses focus (screens that record each change). */
  otherCommit?: 'change' | 'blur'
  /** Put on the first chip so a form can focus the group when it has an error. */
  id?: string
  hideLegend?: boolean
  className?: string
}

const normalize = (o: string | ChoiceOption): ChoiceOption => (typeof o === 'string' ? { value: o } : o)

/**
 * A row of big selectable buttons (native radios underneath, so arrows and Tab work as usual).
 * Optional groups can be cleared by choosing the selected chip again.
 */
export function ChoiceChips({
  label,
  value,
  options,
  onChange,
  required,
  error,
  hint,
  other,
  otherPlaceholder,
  otherMaxLength = 40,
  otherCommit = 'change',
  id,
  hideLegend,
  className,
}: ChoiceChipsProps) {
  const name = useId()
  const hintId = hint || error ? `${name}-hint` : undefined
  const opts = options.map(normalize)
  const known = opts.some((o) => o.value === value)
  const [otherOpen, setOtherOpen] = useState(Boolean(value) && !known)
  const showOther = Boolean(other) && (otherOpen || (Boolean(value) && !known))
  const [draft, setDraft] = useState(known ? '' : value)
  const otherInput = useRef<HTMLInputElement>(null)
  const focusOther = useRef(false)

  useEffect(() => {
    if (!known) setDraft(value)
  }, [value, known])

  useEffect(() => {
    if (showOther && focusOther.current) {
      focusOther.current = false
      otherInput.current?.focus()
    }
  }, [showOther])

  function choose(next: string) {
    setOtherOpen(false)
    onChange(next)
  }

  function toggleOff(chipValue: string) {
    // Choosing the selected chip again clears an optional group.
    if (!required && value === chipValue) onChange('')
  }

  const chipClass = (checked: boolean) =>
    cx(
      'inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center gap-1.5 rounded-xl border-2 px-4 font-bold transition-colors select-none',
      'has-[:focus-visible]:ring-4 has-[:focus-visible]:ring-brand-200',
      checked
        ? 'border-brand-700 bg-brand-700 text-white shadow-sm'
        : error
          ? 'border-rose-300 bg-white text-slate-800 hover:border-brand-400'
          : 'border-slate-300 bg-white text-slate-800 hover:border-brand-400 hover:bg-brand-50',
    )

  return (
    <fieldset className={cx('flex min-w-0 flex-col gap-2', className)}>
      <legend className={cx('mb-2 font-bold text-slate-800', hideLegend && 'sr-only')}>
        {label}
        {required && <span className="text-rose-600" aria-hidden> *</span>}
        {required && <span className="sr-only"> (obrigatório)</span>}
      </legend>
      <div className="flex flex-wrap gap-2">
        {opts.map((o, index) => {
          const checked = value === o.value && !showOther
          return (
            <label key={o.value} className={chipClass(checked)}>
              <input
                type="radio"
                className="sr-only"
                name={name}
                id={index === 0 ? id : undefined}
                value={o.value}
                checked={checked}
                aria-label={o.ariaLabel}
                aria-describedby={hintId}
                aria-invalid={error ? true : undefined}
                onChange={() => choose(o.value)}
                onClick={() => checked && toggleOff(o.value)}
              />
              {checked && <Check className="size-4" aria-hidden />}
              {o.label ?? o.value}
            </label>
          )
        })}
        {other && (
          <label className={chipClass(showOther)}>
            <input
              type="radio"
              className="sr-only"
              name={name}
              value="__other__"
              checked={showOther}
              aria-describedby={hintId}
              onChange={() => {
                focusOther.current = true
                setOtherOpen(true)
                setDraft('')
                if (known) onChange('')
              }}
              onClick={() => {
                if (showOther && !required) {
                  setOtherOpen(false)
                  setDraft('')
                  onChange('')
                }
              }}
            />
            {showOther ? <Check className="size-4" aria-hidden /> : <PenLine className="size-4" aria-hidden />}
            {other}
          </label>
        )}
      </div>
      {showOther && (
        <input
          ref={otherInput}
          className={cx(inputClass, 'max-w-xs')}
          aria-label={`${label}: ${other}`}
          aria-describedby={hintId}
          aria-invalid={error ? true : undefined}
          placeholder={otherPlaceholder}
          maxLength={otherMaxLength}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            if (otherCommit === 'change') onChange(e.target.value)
          }}
          onBlur={() => {
            if (otherCommit === 'blur' && draft !== value) onChange(draft.trim())
          }}
        />
      )}
      {(hint || error) && (
        <p id={hintId} className={cx('text-sm', error ? 'font-bold text-rose-700' : 'text-slate-600')}>
          {error || hint}
        </p>
      )}
    </fieldset>
  )
}

const NUMERALS: Record<Periodo, string[]> = {
  unidade: ['I', 'II', 'III', 'IV'],
  trimestre: ['I', 'II', 'III'],
  periodo: ['I', 'II', 'III', 'IV'],
}

/** "II Unidade", "III Trimestre", "I Período" chosen with two short rows of buttons. */
export function PeriodChoice({
  unidade,
  periodo,
  onChange,
  label = 'Unidade, trimestre ou período',
  className,
}: {
  unidade: string
  periodo: Periodo
  onChange: (value: { unidade: string; periodo: Periodo }) => void
  label?: string
  className?: string
}) {
  const numerals = NUMERALS[periodo]
  return (
    <fieldset className={cx('flex min-w-0 flex-col gap-3', className)}>
      <legend className="mb-2 font-bold text-slate-800">{label}</legend>
      <ChoiceChips
        label="Como a escola chama"
        hideLegend
        value={periodo}
        required
        options={(Object.keys(PERIODOS) as Periodo[]).map((p) => ({ value: p, label: PERIODOS[p] }))}
        onChange={(p) => {
          const next = p as Periodo
          onChange({ unidade: NUMERALS[next].includes(unidade) ? unidade : '', periodo: next })
        }}
      />
      <ChoiceChips
        label={`Qual ${PERIODOS[periodo].toLowerCase()}`}
        hideLegend
        value={numerals.includes(unidade) ? unidade : ''}
        options={numerals.map((n) => ({ value: n, ariaLabel: `${n} ${PERIODOS[periodo]}` }))}
        onChange={(n) => onChange({ unidade: n, periodo })}
        hint="Toque de novo para desmarcar."
      />
    </fieldset>
  )
}

/** "II Unidade" ⇄ { unidade: "II", periodo: "unidade" } for forms that keep the period as text. */
export function parsePeriod(text: string): { unidade: string; periodo: Periodo } {
  const m = /^\s*(I{1,3}|IV)\s+(unidade|trimestre|per[íi]odo)\s*$/i.exec(text)
  if (!m) return { unidade: '', periodo: 'unidade' }
  const word = m[2].toLowerCase()
  return { unidade: m[1].toUpperCase(), periodo: word.startsWith('tri') ? 'trimestre' : word.startsWith('uni') ? 'unidade' : 'periodo' }
}

export function formatPeriod(unidade: string, periodo: Periodo): string {
  return unidade ? `${unidade} ${PERIODOS[periodo]}` : ''
}
