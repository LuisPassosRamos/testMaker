import { Check } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cx } from './ui'

export const STEPS = [
  { slug: 'enviar', label: 'Enviar prova', short: 'Enviar' },
  { slug: 'conferir', label: 'Conferir', short: 'Conferir' },
  { slug: 'ajustar', label: 'Ajustar (opcional)', short: 'Ajustar' },
  { slug: 'finalizar', label: 'Baixar e enviar', short: 'Enviar' },
] as const

const CREATE_STEP = { slug: 'criar', label: 'Criar com IA', short: 'Criar' } as const

export type StepSlug = (typeof STEPS)[number]['slug'] | 'criar'

export const ALL_STEP_SLUGS: StepSlug[] = ['criar', ...STEPS.map((s) => s.slug)]

/** Exams created with AI start at "Criar com IA" instead of "Enviar prova". */
export function stepsFor(startMode: string) {
  return startMode === 'generate' ? [CREATE_STEP, ...STEPS.slice(1)] : [...STEPS]
}

export function Stepper({ projectId, current, reachable, startMode = 'upload' }: {
  projectId: string
  current: StepSlug
  reachable: number
  startMode?: string
}) {
  const steps = stepsFor(startMode)
  const currentIndex = steps.findIndex((s) => s.slug === current)
  return (
    <nav aria-label="Etapas" className="w-full">
      <ol className="flex items-center gap-1 sm:gap-2">
        {steps.map((step, i) => {
          const done = i < currentIndex
          const active = i === currentIndex
          const enabled = i <= reachable
          const content = (
            <>
              <span
                className={cx(
                  'grid size-9 shrink-0 place-items-center rounded-full border-2 font-display text-base font-extrabold',
                  active && 'border-brand-700 bg-brand-700 text-white',
                  done && 'border-emerald-600 bg-emerald-600 text-white',
                  !active && !done && 'border-slate-300 bg-white text-slate-500',
                )}
                aria-hidden
              >
                {done ? <Check className="size-5" /> : i + 1}
              </span>
              <span className={cx('hidden font-bold md:inline', active ? 'text-brand-900' : 'text-slate-600')}>{step.label}</span>
              <span className={cx('text-sm font-bold md:hidden', active ? 'text-brand-900' : 'sr-only')}>{step.short}</span>
            </>
          )
          return (
            <li key={step.slug} className="flex flex-1 items-center gap-1 sm:gap-2">
              {enabled && !active ? (
                <Link
                  to={`/prova/${projectId}/${step.slug}`}
                  className="flex min-h-11 items-center gap-2 rounded-xl px-1 hover:bg-brand-50"
                  aria-label={`Etapa ${i + 1}: ${step.label}${done ? ' (concluída)' : ''}`}
                >
                  {content}
                </Link>
              ) : (
                <span
                  className={cx('flex min-h-11 items-center gap-2 px-1', !enabled && 'opacity-60')}
                  aria-current={active ? 'step' : undefined}
                  aria-label={`Etapa ${i + 1}: ${step.label}${active ? ' (etapa atual)' : ''}`}
                >
                  {content}
                </span>
              )}
              {i < steps.length - 1 && <span className={cx('h-0.5 flex-1 rounded', done ? 'bg-emerald-500' : 'bg-slate-200')} aria-hidden />}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
