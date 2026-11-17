import { AlertTriangle, Check, CloudOff, Loader2, Redo2, Undo2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { STEPS, Stepper, type StepSlug } from '../components/Stepper'
import { Alert, Button, IconButton, Spinner } from '../components/ui'
import { DOC_TYPES, examTitle } from '../lib/labels'
import { forgetProject, rememberProject } from '../lib/recent'
import { clearDraft, readDraft, useExamStore } from '../store/examStore'
import FinishStep from './steps/FinishStep'
import LayoutStep from './steps/LayoutStep'
import ReviewStep from './steps/ReviewStep'
import UploadStep from './steps/UploadStep'

function SaveIndicator() {
  const { saveState, saveError } = useExamStore()
  const states = {
    saved: { icon: <Check className="size-4" />, text: 'Tudo salvo', cls: 'text-emerald-700' },
    dirty: { icon: <Loader2 className="size-4 animate-spin" />, text: 'Salvando…', cls: 'text-slate-500' },
    saving: { icon: <Loader2 className="size-4 animate-spin" />, text: 'Salvando…', cls: 'text-slate-500' },
    error: { icon: <CloudOff className="size-4" />, text: 'Sem conexão — guardado neste navegador', cls: 'text-amber-700' },
    conflict: { icon: <AlertTriangle className="size-4" />, text: 'Alterado em outra aba', cls: 'text-rose-700' },
  }[saveState]
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-bold ${states.cls}`} role="status" title={saveError ?? undefined}>
      {states.icon}
      {states.text}
    </span>
  )
}

export default function ProjectPage() {
  const { id = '', step = 'conferir' } = useParams()
  const navigate = useNavigate()
  const { project, exam, load, apply, undo, redo, past, future, saveState } = useExamStore()
  const [error, setError] = useState<string | null>(null)
  const [draftOffer, setDraftOffer] = useState(false)

  useEffect(() => {
    let alive = true
    setError(null)
    api
      .getProject(id)
      .then((p) => {
        if (!alive) return
        load(p)
        rememberProject(p.id)
        const draft = readDraft(p.id)
        if (draft && draft.revision === p.revision && JSON.stringify(draft.exam) !== JSON.stringify(p.exam)) {
          setDraftOffer(true)
        } else if (draft) {
          clearDraft(p.id)
        }
      })
      .catch((err) => {
        if (!alive) return
        if (err instanceof ApiError && err.status === 404) forgetProject(id)
        setError(err instanceof Error ? err.message : 'Erro ao abrir a prova.')
      })
    return () => {
      alive = false
    }
  }, [id, load])

  // Ctrl+Z / Ctrl+Y outside text fields (the text editor has its own undo while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.closest('input, textarea, select, [contenteditable="true"]')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
        e.preventDefault()
        undo()
      } else if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  if (!STEPS.some((s) => s.slug === step)) return <Navigate to={`/prova/${id}/conferir`} replace />

  if (error) {
    return (
      <main id="conteudo" className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
        <Alert tone="error" title="Não foi possível abrir" action={<Button onClick={() => navigate('/')}>Voltar ao início</Button>}>
          {error}
        </Alert>
      </main>
    )
  }
  if (!project || !exam || project.id !== id) {
    return (
      <main id="conteudo" className="grid flex-1 place-items-center p-10">
        <Spinner label="Abrindo…" />
      </main>
    )
  }

  const hasContent = exam.blocks.length > 0
  const reachable = hasContent || project.start_mode !== 'upload' ? 3 : 0
  const current = step as StepSlug
  if (!hasContent && project.start_mode === 'upload' && current !== 'enviar') {
    return <Navigate to={`/prova/${id}/enviar`} replace />
  }
  const showHistory = current === 'conferir' || current === 'ajustar'

  return (
    <main id="conteudo" className="flex flex-1 flex-col">
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-[96rem] flex-col gap-3 px-4 py-4 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-bold tracking-wide text-brand-700 uppercase">{DOC_TYPES[exam.meta.doc_type].label}</p>
              <h1 className="truncate text-2xl font-extrabold text-brand-950">{examTitle(exam.meta)}</h1>
            </div>
            <div className="flex items-center gap-2">
              {showHistory && (
                <>
                  <IconButton label="Desfazer (Ctrl+Z)" onClick={undo} disabled={!past.length}>
                    <Undo2 className="size-5" />
                  </IconButton>
                  <IconButton label="Refazer (Ctrl+Y)" onClick={redo} disabled={!future.length}>
                    <Redo2 className="size-5" />
                  </IconButton>
                </>
              )}
              <SaveIndicator />
            </div>
          </div>
          <Stepper projectId={id} current={current} reachable={reachable} />
        </div>
      </div>

      <div className="mx-auto w-full max-w-[96rem] flex-1 px-4 py-6 sm:px-6">
        {saveState === 'conflict' && (
          <Alert
            tone="error"
            className="mb-4"
            title="Esta prova foi alterada em outra aba ou computador"
            action={<Button onClick={() => window.location.reload()}>Recarregar a versão mais recente</Button>}
          >
            Para não sobrescrever o trabalho de ninguém, as próximas alterações desta aba não serão salvas.
          </Alert>
        )}
        {draftOffer && (
          <Alert
            tone="warning"
            className="mb-4"
            title="Encontramos alterações que não chegaram a ser salvas"
            action={
              <>
                <Button
                  onClick={() => {
                    const draft = readDraft(id)
                    if (draft) apply((d) => Object.assign(d, draft.exam), { kind: 'content', summary: 'Alterações recuperadas deste navegador' })
                    setDraftOffer(false)
                  }}
                >
                  Recuperar alterações
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    clearDraft(id)
                    setDraftOffer(false)
                  }}
                >
                  Descartar
                </Button>
              </>
            }
          >
            Elas ficaram guardadas neste navegador (por exemplo, por falta de conexão).
          </Alert>
        )}
        {current === 'enviar' && <UploadStep />}
        {current === 'conferir' && <ReviewStep />}
        {current === 'ajustar' && <LayoutStep />}
        {current === 'finalizar' && <FinishStep />}
      </div>
    </main>
  )
}
