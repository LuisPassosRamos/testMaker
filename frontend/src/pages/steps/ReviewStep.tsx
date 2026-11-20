import { AlertTriangle, ArrowRight, Bot, CheckCircle2, Eye, Filter, Info, ListOrdered, Plus, Ruler, UserPen } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../../api/client'
import type { Block, BlockKind, Suggestion } from '../../api/types'
import { Dialog, confirmDialog } from '../../components/Dialog'
import { toast } from '../../components/Toast'
import { Alert, Badge, Button, Toggle, cx } from '../../components/ui'
import { useSystem } from '../../hooks/useHealth'
import {
  allOpenIssues,
  findPart,
  insertBlock,
  insertPart,
  isEdited,
  makeBlock,
  makePart,
  nextQuestionNumber,
  openIssues,
  resolveIssue,
  setPartKind,
  setPartRuns,
  splitBlockAt,
} from '../../lib/exam-ops'
import { BLOCK_KINDS } from '../../lib/labels'
import { flushSave, useExamStore } from '../../store/examStore'
import { BlockCard, IssueNote } from '../review/BlockCard'
import { OriginalViewer } from '../review/OriginalViewer'
import { SimpleReview } from '../review/SimpleReview'

function InsertBar({ afterId, onInsert }: { afterId: string | null; onInsert: (kind: BlockKind, afterId: string | null) => void }) {
  return (
    <div className="group flex items-center gap-2 py-1" role="group" aria-label="Adicionar conteúdo aqui">
      <span className="h-px flex-1 bg-slate-200 group-hover:bg-brand-300" aria-hidden />
      {(['question', 'support_text', 'page_break'] as BlockKind[]).map((kind) => (
        <button
          key={kind}
          onClick={() => onInsert(kind, afterId)}
          className="inline-flex min-h-8 items-center gap-1 rounded-full border border-slate-200 bg-white px-3 text-sm font-bold text-slate-500 hover:border-brand-300 hover:text-brand-800 focus:text-brand-800"
        >
          <Plus className="size-3.5" aria-hidden />
          {BLOCK_KINDS[kind].label}
        </button>
      ))}
      <span className="h-px flex-1 bg-slate-200 group-hover:bg-brand-300" aria-hidden />
    </div>
  )
}

function questionStatus(block: Block): 'error' | 'warning' | 'edited' | 'ok' | 'todo' {
  const issues = openIssues(block)
  if (issues.some((i) => i.severity === 'error')) return 'error'
  if (issues.some((i) => i.severity === 'warning')) return 'warning'
  if (block.reviewed) return 'ok'
  if (block.parts.some(isEdited)) return 'edited'
  return 'todo'
}

const MODE_KEY = 'prova-pronta:review-mode'

function savedMode(): 'simple' | 'advanced' {
  try {
    return localStorage.getItem(MODE_KEY) === 'advanced' ? 'advanced' : 'simple'
  } catch {
    return 'simple'
  }
}

/** Simple view by default (for everyone); the detailed editor is one click away and remembered. */
export default function ReviewStep() {
  const [mode, setMode] = useState<'simple' | 'advanced'>(savedMode)
  const change = (next: 'simple' | 'advanced') => {
    setMode(next)
    try {
      localStorage.setItem(MODE_KEY, next)
    } catch {
      /* ignore */
    }
    window.scrollTo({ top: 0 })
  }
  return mode === 'simple' ? <SimpleReview onAdvanced={() => change('advanced')} /> : <AdvancedReview onSimple={() => change('simple')} />
}

function AdvancedReview({ onSimple }: { onSimple: () => void }) {
  const { project, exam, apply, select, selectedPartId } = useExamStore()
  const navigate = useNavigate()
  const health = useSystem((s) => s.health)
  const [onlyPending, setOnlyPending] = useState(false)
  const [cropTarget, setCropTarget] = useState<string | null>(null)
  const [aiOpen, setAiOpen] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null)

  const focus = useMemo(() => {
    if (!exam || !selectedPartId) return null
    return findPart(exam, selectedPartId)?.part.provenance ?? null
  }, [exam, selectedPartId])

  if (!project || !exam) return null
  const pending = allOpenIssues(exam)
  const questions = exam.blocks.filter((b) => b.kind === 'question')
  const targets = exam.blocks
    .filter((b) => b.kind !== 'page_break')
    .map((b) => ({ id: b.id, label: b.kind === 'question' ? `Questão ${b.number || '?'}` : BLOCK_KINDS[b.kind].label }))
  const editedCount = exam.blocks.reduce((n, b) => n + b.parts.filter(isEdited).length, 0)
  const visible = onlyPending ? exam.blocks.filter((b) => openIssues(b).length > 0) : exam.blocks
  const hasOriginal = project.sources.some((s) => s.pages.length > 0)

  function goToNextIssue() {
    const blockIds = exam!.blocks.filter((b) => openIssues(b).length).map((b) => b.id)
    if (!blockIds.length) return
    const currentBlock = selectedPartId ? exam!.blocks.find((b) => b.parts.some((p) => p.id === selectedPartId))?.id : null
    const next = blockIds[(blockIds.indexOf(currentBlock ?? '') + 1) % blockIds.length]
    const block = exam!.blocks.find((b) => b.id === next)!
    const part = block.parts.find((p) => p.issues.some((i) => !i.resolved)) ?? block.parts[0]
    if (part) select(part.id)
    document.getElementById(`bloco-${next}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function insert(kind: BlockKind, afterId: string | null) {
    const afterIndex = afterId ? exam!.blocks.findIndex((b) => b.id === afterId) + 1 : 0
    const block = makeBlock(kind, kind === 'question' ? { number: nextQuestionNumber(exam!, afterIndex), parts: [makePart('paragraph')] } : kind === 'page_break' ? {} : { parts: [makePart('paragraph')] })
    apply((d) => insertBlock(d, afterId, block), { kind: kind === 'page_break' ? 'layout' : 'structure', summary: `${BLOCK_KINDS[kind].label} adicionado(a)` })
    setTimeout(() => document.getElementById(`bloco-${block.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50)
  }

  async function handleCrop(sourceId: string, page: number, bbox: [number, number, number, number]) {
    try {
      const res = await api.crop(project!.id, sourceId, page, bbox)
      const provenance = { source_id: sourceId, page, bbox, method: 'crop' as const, confidence: null }
      const target = cropTarget ?? selectedPartId
      apply(
        (d) => {
          const found = target ? findPart(d, target) : undefined
          if (found && (found.part.kind === 'crop' || found.part.kind === 'image') && !found.part.asset_id) {
            Object.assign(found.part, { asset_id: res.asset_id, width_cm: res.width_cm, provenance, kind: 'crop' })
            found.part.issues.forEach((i) => (i.resolved = true))
          } else if (found) {
            insertPart(d, found.block.id, found.part.id, makePart('crop', { asset_id: res.asset_id, width_cm: res.width_cm, provenance }))
          } else {
            const block = makeBlock('support_text', { parts: [makePart('crop', { asset_id: res.asset_id, width_cm: res.width_cm, provenance })] })
            insertBlock(d, d.blocks[d.blocks.length - 1]?.id ?? null, block)
          }
        },
        { kind: 'content', summary: 'Recorte do original inserido' },
      )
      setCropTarget(null)
      toast.success('Recorte inserido.')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Não foi possível recortar.')
    }
  }

  async function runAi(kind: 'structure' | 'ocr') {
    setAiBusy(true)
    try {
      setSuggestions(kind === 'structure' ? await api.aiStructure(project!.id) : await api.aiOcrReview(project!.id))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'A IA não respondeu.')
    } finally {
      setAiBusy(false)
    }
  }

  function applySuggestion(s: Suggestion) {
    apply(
      (d) => {
        switch (s.action) {
          case 'make_alternative':
            setPartKind(d, s.part_id, 'alternative')
            break
          case 'make_paragraph':
            setPartKind(d, s.part_id, 'paragraph')
            break
          case 'split_as_question':
            splitBlockAt(d, s.part_id, 'question')
            break
          case 'split_as_support':
            splitBlockAt(d, s.part_id, 'support_text')
            break
          case 'replace_text':
            if (s.suggestion) setPartRuns(d, s.part_id, [{ text: s.suggestion }])
            break
        }
      },
      { kind: s.kind === 'ocr' ? 'content' : 'structure', summary: `Sugestão da IA aplicada: ${s.message}`, blockId: s.block_id },
    )
    setSuggestions((list) => list?.filter((x) => x !== s) ?? null)
  }

  async function next() {
    const errors = pending.filter((p) => p.issue.severity === 'error').length
    if (errors) {
      const ok = await confirmDialog({
        title: 'Ainda há trechos para revisar',
        message: `${errors} trecho(s) estão marcados como “Precisa revisar”. Você pode continuar e voltar depois — eles aparecem também antes do envio.`,
        confirm: 'Continuar mesmo assim',
        cancel: 'Voltar e revisar',
      })
      if (!ok) return
    }
    await flushSave()
    navigate(`/prova/${project!.id}/finalizar`)
  }

  const chipStyle = {
    error: 'border-rose-300 bg-rose-50 text-rose-800',
    warning: 'border-amber-300 bg-amber-50 text-amber-900',
    edited: 'border-violet-300 bg-violet-50 text-violet-800',
    ok: 'border-emerald-300 bg-emerald-50 text-emerald-800',
    todo: 'border-slate-200 bg-white text-slate-700',
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-extrabold text-brand-950">Confira o conteúdo</h2>
          <p className="mt-1 max-w-3xl text-slate-600">
            Compare com o original ao lado. Clique em qualquer texto para corrigir. Nada foi reescrito: o que estiver diferente do papel,
            corrija aqui.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" icon={<Eye className="size-5" />} onClick={onSimple}>
            Ver como vai ficar
          </Button>
          {health?.ai.enabled && (
            <Button variant="secondary" icon={<Bot className="size-5" />} onClick={() => setAiOpen(true)}>
              Sugestões da IA
            </Button>
          )}
          <Button variant="secondary" icon={<AlertTriangle className="size-5" />} disabled={!pending.length} onClick={goToNextIssue}>
            Próxima pendência
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-card">
        <span className="flex items-center gap-2 font-bold text-slate-800">
          {pending.length ? (
            <>
              <AlertTriangle className="size-5 text-amber-600" aria-hidden /> {pending.length} ponto(s) para revisar
            </>
          ) : (
            <>
              <CheckCircle2 className="size-5 text-emerald-600" aria-hidden /> Nenhuma pendência
            </>
          )}
        </span>
        <span className="flex flex-wrap items-center gap-2 text-sm" aria-label="Legenda">
          <Badge tone="rose" icon={<AlertTriangle className="size-3.5" aria-hidden />}>
            Leitura duvidosa
          </Badge>
          <Badge tone="violet" icon={<UserPen className="size-3.5" aria-hidden />}>
            Corrigido por você ({editedCount})
          </Badge>
          <Badge tone="teal" icon={<Ruler className="size-3.5" aria-hidden />}>
            Diagramação
          </Badge>
        </span>
        <span className="ml-auto">
          <Toggle label="Mostrar só pendências" checked={onlyPending} onChange={setOnlyPending} />
        </span>
      </div>

      {questions.length > 0 && (
        <nav aria-label="Ir para a questão" className="flex items-center gap-2 overflow-x-auto pb-1">
          <ListOrdered className="size-5 shrink-0 text-slate-500" aria-hidden />
          {questions.map((q) => {
            const status = questionStatus(q)
            return (
              <button
                key={q.id}
                onClick={() => {
                  if (q.parts[0]) select(q.parts[0].id)
                  document.getElementById(`bloco-${q.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }}
                className={cx('min-h-10 shrink-0 rounded-xl border-2 px-3 font-display font-extrabold', chipStyle[status])}
                aria-label={`Questão ${q.number}${status === 'error' || status === 'warning' ? ' (tem pendências)' : status === 'ok' ? ' (conferida)' : ''}`}
              >
                {q.number || '?'}
              </button>
            )
          })}
        </nav>
      )}

      {exam.document_issues.some((i) => !i.resolved) && (
        <div className="flex flex-col gap-2">
          {exam.document_issues
            .filter((i) => !i.resolved)
            .map((issue) => (
              <IssueNote key={issue.id} issue={issue} onResolve={() => apply((d) => resolveIssue(d, issue.id))} />
            ))}
        </div>
      )}

      <div className={cx('grid gap-5', hasOriginal && 'lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]')}>
        {hasOriginal && (
          <div className="lg:sticky lg:top-3 lg:h-[calc(100dvh-1.5rem)]">
            <div className="h-[70dvh] lg:h-full">
              <OriginalViewer project={project} focus={focus} onCrop={handleCrop} />
            </div>
          </div>
        )}
        <div className="flex min-w-0 flex-col">
          {!hasOriginal && project.sources.length === 0 && (
            <Alert tone="info" className="mb-3" title="Monte o conteúdo aqui">
              Use os botões “+ Questão”, “+ Texto de apoio” e, dentro de cada questão, “+ Alternativa” e “+ Imagem”.
            </Alert>
          )}
          {cropTarget && (
            <Alert tone="info" className="mb-3" title="Recorte do original">
              Use “Recortar do original” no painel ao lado e marque a área que deve ficar como imagem.
            </Alert>
          )}
          <InsertBar afterId={null} onInsert={insert} />
          {visible.map((block) => {
            const index = exam.blocks.indexOf(block)
            return (
              <div key={block.id}>
                <BlockCard
                  block={block}
                  index={index}
                  total={exam.blocks.length}
                  targets={targets}
                  projectId={project.id}
                  onRequestCrop={(partId) => {
                    setCropTarget(partId)
                    select(partId)
                  }}
                />
                <InsertBar afterId={block.id} onInsert={insert} />
              </div>
            )
          })}
          {visible.length === 0 && onlyPending && (
            <Alert tone="success" title="Nenhuma pendência">
              Desligue o filtro para ver todo o conteúdo.
            </Alert>
          )}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
            <p className="flex items-center gap-2 text-slate-600">
              <Info className="size-5 text-brand-600" aria-hidden /> Tudo certo? A diagramação é automática; ajustar é opcional.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => void flushSave().then(() => navigate(`/prova/${project.id}/ajustar`))}>
                Ajustar impressão
              </Button>
              <Button size="lg" icon={<ArrowRight className="size-5" />} onClick={() => void next()}>
                Baixar e enviar
              </Button>
            </div>
          </div>
        </div>
      </div>

      <Dialog
        open={aiOpen}
        title="Revisão com IA (opcional)"
        size="lg"
        onClose={() => {
          setAiOpen(false)
          setSuggestions(null)
        }}
      >
        {suggestions === null ? (
          <div className="flex flex-col gap-4">
            <Alert tone="info" title="Sugestões uma a uma">
              {health?.ai.notice} A IA só sugere — nada é alterado sem você clicar em “Aplicar”, e ela nunca reescreve ou resolve questões.
            </Alert>
            <div className="grid gap-3 sm:grid-cols-2">
              <Button variant="secondary" size="lg" loading={aiBusy} icon={<Filter className="size-5" />} onClick={() => void runAi('structure')}>
                Conferir a organização
              </Button>
              <Button variant="secondary" size="lg" loading={aiBusy} icon={<Bot className="size-5" />} onClick={() => void runAi('ocr')}>
                Conferir a leitura das fotos
              </Button>
            </div>
          </div>
        ) : suggestions.length === 0 ? (
          <Alert tone="success" title="Nenhuma sugestão">
            A IA não encontrou nada para sugerir.
          </Alert>
        ) : (
          <ul className="flex flex-col gap-3">
            {suggestions.map((s, i) => (
              <li key={i} className="rounded-xl border border-slate-200 p-3">
                <p className="font-bold text-slate-800">{s.message}</p>
                {s.original && s.suggestion && (
                  <div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                    <p className="rounded bg-rose-50 p-2">
                      <span className="block font-bold text-rose-800">Atual</span>
                      {s.original}
                    </p>
                    <p className="rounded bg-emerald-50 p-2">
                      <span className="block font-bold text-emerald-800">Sugerido</span>
                      {s.suggestion}
                    </p>
                  </div>
                )}
                <div className="mt-2 flex gap-2">
                  {s.action && (
                    <Button size="sm" onClick={() => applySuggestion(s)}>
                      Aplicar
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => setSuggestions((list) => list?.filter((x) => x !== s) ?? null)}>
                    Ignorar
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      select(s.part_id)
                      document.getElementById(`bloco-${s.block_id}`)?.scrollIntoView({ behavior: 'smooth' })
                      setAiOpen(false)
                    }}
                  >
                    Ver no conteúdo
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Dialog>
    </div>
  )
}
