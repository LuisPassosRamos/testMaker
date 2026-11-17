import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react'
import { urls } from '../../api/client'
import type { CheckResult, JobState, RenderStatus } from '../../api/types'
import { Alert, Button, cx } from '../../components/ui'

const ICON = {
  error: <XCircle className="size-5 shrink-0 text-rose-700" aria-hidden />,
  warning: <AlertTriangle className="size-5 shrink-0 text-amber-700" aria-hidden />,
  info: <Info className="size-5 shrink-0 text-slate-500" aria-hidden />,
}

export function CheckList({
  checks,
  onOpenBlock,
  onNewPage,
}: {
  checks: CheckResult[]
  onOpenBlock?: (blockId: string) => void
  onNewPage?: (blockId: string) => void
}) {
  if (!checks.length) {
    return (
      <Alert tone="success" title="Verificação automática: tudo certo">
        Todas as questões e alternativas estão no PDF, na ordem, dentro das margens e em folha A4.
      </Alert>
    )
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="Resultado da verificação automática">
      {checks.map((c, i) => (
        <li
          key={i}
          className={cx(
            'flex flex-wrap items-start gap-2 rounded-xl border px-3 py-2',
            c.severity === 'error' ? 'border-rose-300 bg-rose-50' : c.severity === 'warning' ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-slate-50',
          )}
        >
          {ICON[c.severity]}
          <p className="flex-1 text-[0.95rem]">{c.message}</p>
          {c.block_id && c.code === 'orphan_question_start' && onNewPage && (
            <Button size="sm" variant="secondary" onClick={() => onNewPage(c.block_id!)}>
              Começar em nova página
            </Button>
          )}
          {c.block_id && onOpenBlock && (
            <Button size="sm" variant="ghost" onClick={() => onOpenBlock(c.block_id!)}>
              Ver questão
            </Button>
          )}
        </li>
      ))}
    </ul>
  )
}

export function PdfPreview({
  projectId,
  status,
  rendering,
  job,
  error,
  onRetry,
}: {
  projectId: string
  status: RenderStatus | null
  rendering: boolean
  job: JobState | null
  error: string | null
  onRetry: () => void
}) {
  const render = status?.render
  return (
    <div className="relative">
      <div className="sticky top-0 z-10 mb-3 flex items-center gap-2 rounded-xl bg-white/90 px-3 py-2 shadow-card backdrop-blur" aria-live="polite">
        {rendering ? (
          <>
            <Loader2 className="size-5 animate-spin text-brand-700" aria-hidden />
            <span className="font-bold text-brand-900">{job?.message || 'Atualizando a prévia…'}</span>
          </>
        ) : render && status?.up_to_date ? (
          <>
            <CheckCircle2 className="size-5 text-emerald-600" aria-hidden />
            <span className="font-bold text-slate-800">
              Prévia real do PDF · {render.pages} {render.pages === 1 ? 'página' : 'páginas'}
            </span>
          </>
        ) : (
          <span className="font-bold text-slate-600">A prévia será atualizada em instantes…</span>
        )}
      </div>
      {error && (
        <Alert tone="error" className="mb-3" title="A prévia não foi gerada" action={<Button size="sm" onClick={onRetry}>Tentar de novo</Button>}>
          {error}
        </Alert>
      )}
      {render ? (
        <div className={cx('flex flex-col items-center gap-6 transition-opacity', rendering && 'opacity-50')}>
          {Array.from({ length: render.pages }, (_, i) => (
            <figure key={`${render.render_id}-${i}`} className="w-full max-w-[44rem]">
              <img
                src={urls.renderPage(projectId, render.render_id, i + 1)}
                alt={`Página ${i + 1} de ${render.pages} da prova como será impressa`}
                className="w-full rounded-sm bg-white shadow-lift ring-1 ring-slate-200"
                loading={i > 1 ? 'lazy' : 'eager'}
              />
              <figcaption className="mt-2 text-center text-sm font-bold text-slate-500">
                Página {i + 1} de {render.pages}
              </figcaption>
            </figure>
          ))}
        </div>
      ) : (
        !error && (
          <div className="grid aspect-[210/297] w-full max-w-[44rem] place-items-center rounded-sm bg-white shadow-card ring-1 ring-slate-200">
            <span className="flex items-center gap-2 text-slate-500">
              <Loader2 className="size-5 animate-spin" aria-hidden /> Gerando a primeira prévia…
            </span>
          </div>
        )
      )}
    </div>
  )
}
