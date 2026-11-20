/**
 * "Confira sua prova": the exam shown as it will be printed, with one or two clicks per fix.
 *
 * Teachers who are not comfortable with technology should not have to think in blocks and
 * part types. They read the exam, answer the few doubts listed on top, and tap a question
 * to fix it with plain-language buttons. The detailed editor stays one click away.
 */
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Check,
  CheckCircle2,
  Combine,
  Eye,
  EyeOff,
  FileWarning,
  ImagePlus,
  KeyRound,
  ListPlus,
  Minus,
  PencilLine,
  Plus,
  RefreshCw,
  Sparkles,
  ThumbsDown,
  Trash2,
  TrendingDown,
  TrendingUp,
  Wand2,
  X,
} from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api, urls } from '../../api/client'
import type { AnswerSpace, Block, Exam, Issue, Part } from '../../api/types'
import { confirmDialog } from '../../components/Dialog'
import { ProgressCard } from '../../components/ProgressCard'
import { toast } from '../../components/Toast'
import { Alert, Button, Card, cx, inputClass } from '../../components/ui'
import { useSystem } from '../../hooks/useHealth'
import { useJob } from '../../hooks/useJob'
import { changeBlockKind, deleteBlock, findBlock, insertPart, makePart, mergeWithPrevious, resolveIssue } from '../../lib/exam-ops'
import { examTitle } from '../../lib/labels'
import { runsText } from '../../lib/runs'
import { flushSave, useExamStore } from '../../store/examStore'
import { BlockCard } from './BlockCard'
import { OriginalViewer } from './OriginalViewer'
import { RunsView } from './RunsView'

type Point = { issue: Issue; block: Block | null }

/** Doubts worth a teacher's attention: warnings and errors (notes stay in the detailed editor). */
export function pointsToCheck(exam: Exam): Point[] {
  const points: Point[] = exam.document_issues
    .filter((i) => !i.resolved && i.severity !== 'info')
    .map((issue) => ({ issue, block: null }))
  for (const block of exam.blocks) {
    for (const issue of [...block.issues, ...block.parts.flatMap((p) => p.issues)]) {
      if (!issue.resolved && issue.severity !== 'info') points.push({ issue, block })
    }
  }
  return points
}

function blockName(block: Block): string {
  if (block.kind === 'question') return `Questão ${(block.number ?? '').replace(/[\s.)\-–:]+$/, '') || 'sem número'}`
  return { support_text: 'Texto de apoio', instructions: 'Instruções', original_header: 'Cabeçalho do arquivo', free: 'Trecho', page_break: 'Quebra de página', question: 'Questão' }[block.kind]
}

// ------------------------------------------------------------------ printing look

function AnswerView({ space }: { space: AnswerSpace }) {
  if (space.kind === 'none') return null
  return (
    <div className="mt-2" aria-label="Espaço para a resposta do aluno">
      {space.label && <p className="text-sm font-bold">{space.label}</p>}
      {space.kind === 'lines' &&
        Array.from({ length: Math.min(space.lines, 20) }, (_, i) => <div key={i} className="h-7 border-b border-slate-400" />)}
      {space.kind === 'box' && <div className="rounded border border-slate-500" style={{ height: `${Math.min(space.height_cm, 12) * 1.6}rem` }} />}
      {space.kind === 'blank' && (
        <div className="rounded border border-dashed border-slate-300" style={{ height: `${Math.min(space.height_cm, 12) * 1.6}rem` }} />
      )}
    </div>
  )
}

function PartView({ part, projectId }: { part: Part; projectId: string }) {
  if (part.kind === 'paragraph') {
    return (
      <p className="leading-relaxed" style={{ textAlign: part.align ?? undefined }}>
        <RunsView runs={part.runs} />
      </p>
    )
  }
  if (part.kind === 'image' || part.kind === 'crop') {
    return part.asset_id ? (
      <img
        src={urls.asset(projectId, part.asset_id)}
        alt="Imagem da prova"
        className="my-2 max-w-full grayscale"
        style={{ width: part.width_cm ? `${Math.min(100, (part.width_cm / 18) * part.width_pct)}%` : `${part.width_pct}%` }}
      />
    ) : (
      <p className="my-2 rounded border border-dashed border-rose-300 p-3 text-sm text-rose-700">Imagem ainda não inserida.</p>
    )
  }
  if (part.kind === 'table' && part.rows) {
    return (
      <table className={cx('my-2 w-full border-collapse text-[0.95em]', part.table_borders && 'border border-slate-500')}>
        <tbody>
          {part.rows.map((row, r) => (
            <tr key={r}>
              {row
                .filter((cell) => cell.vmerge !== 'continue')
                .map((cell, c) => (
                  <td key={c} colSpan={cell.colspan} className={cx('px-2 py-1 align-top', part.table_borders && 'border border-slate-500')}>
                    {cell.content.map((content, k) =>
                      content.asset_id ? (
                        <img key={k} src={urls.asset(projectId, content.asset_id)} alt="" className="max-w-full grayscale" />
                      ) : (
                        <span key={k} className="block">
                          <RunsView runs={content.runs} />
                        </span>
                      ),
                    )}
                  </td>
                ))}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }
  if (part.kind === 'answer_space' && part.answer) return <AnswerView space={part.answer} />
  return null
}

function QuestionView({ block, projectId }: { block: Block; projectId: string }) {
  const alternatives = block.parts.filter((p) => p.kind === 'alternative')
  const short =
    alternatives.length > 0 && alternatives.every((a) => runsText(a.runs).length <= (/^\(\s*\)$/.test(a.label ?? '') ? 20 : 38))
  const sideBySide = block.alternatives_layout === 'row' || (block.alternatives_layout === 'auto' && short)
  const groups: (Part | Part[])[] = []
  for (const part of block.parts) {
    const last = groups[groups.length - 1]
    if (part.kind === 'alternative' && Array.isArray(last)) last.push(part)
    else groups.push(part.kind === 'alternative' ? [part] : part)
  }
  return (
    <div>
      {groups.map((group, i) => {
        if (Array.isArray(group)) {
          return (
            <div key={i} className={cx('my-1.5 gap-x-6 gap-y-1', sideBySide ? 'flex flex-wrap' : 'flex flex-col')}>
              {group.map((alt) => (
                <span key={alt.id} className="leading-relaxed">
                  <strong>{alt.label}</strong> <RunsView runs={alt.runs} />
                </span>
              ))}
            </div>
          )
        }
        const first = i === 0 && group.kind === 'paragraph'
        return first ? (
          <p key={group.id} className="leading-relaxed" style={{ textAlign: group.align ?? undefined }}>
            <strong className="mr-1">{block.number}</strong>
            <RunsView runs={group.runs} />
          </p>
        ) : (
          <div key={group.id}>
            {i === 0 && <strong className="mr-1">{block.number}</strong>}
            <PartView part={group} projectId={projectId} />
          </div>
        )
      })}
      {block.parts.length === 0 && <strong>{block.number}</strong>}
      <AnswerView space={block.answer_space} />
    </div>
  )
}

function BlockView({ block, projectId }: { block: Block; projectId: string }) {
  if (block.kind === 'question') return <QuestionView block={block} projectId={projectId} />
  if (block.kind === 'page_break') {
    return <p className="border-t-2 border-dashed border-slate-300 pt-1 text-center text-xs font-bold text-slate-400 uppercase">Nova página</p>
  }
  const content = block.parts.map((p) => <PartView key={p.id} part={p} projectId={projectId} />)
  if (block.kind === 'support_text') return <div className="rounded-lg border border-slate-300 bg-slate-50 px-4 py-3">{content}</div>
  if (block.kind === 'instructions') {
    return (
      <div className="text-[0.95em]">
        <p className="font-bold">Instruções</p>
        {content}
      </div>
    )
  }
  if (block.kind === 'original_header') {
    return <div className="text-sm text-slate-500">{content}</div>
  }
  return <div>{content}</div>
}

// ------------------------------------------------------------------ actions

function Action({ icon, children, onClick, tone = 'default', disabled }: {
  icon: ReactNode
  children: ReactNode
  onClick: () => void
  tone?: 'default' | 'danger' | 'ai'
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cx(
        'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border px-3 text-sm font-bold transition-colors disabled:opacity-50',
        tone === 'danger' && 'border-rose-200 bg-white text-rose-700 hover:bg-rose-50',
        tone === 'ai' && 'border-violet-200 bg-violet-50 text-violet-800 hover:bg-violet-100',
        tone === 'default' && 'border-brand-200 bg-white text-brand-800 hover:bg-brand-50',
      )}
    >
      {icon}
      {children}
    </button>
  )
}

export function SimpleReview({ onAdvanced }: { onAdvanced: () => void }) {
  const { project, exam, apply, replaceProject } = useExamStore()
  const navigate = useNavigate()
  const health = useSystem((s) => s.health)
  const { job, track } = useJob(project?.id)
  const [selected, setSelected] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [showAnswer, setShowAnswer] = useState<Record<string, boolean>>({})
  const [showOriginal, setShowOriginal] = useState(false)
  const [aiWorking, setAiWorking] = useState<string | null>(null)
  const imageInput = useRef<HTMLInputElement>(null)
  const imageTarget = useRef<{ blockId: string; issueId?: string } | null>(null)

  if (!project || !exam) return null
  const aiReady = Boolean(health?.ai.enabled)
  const points = pointsToCheck(exam)
  const hasOriginal = project.sources.some((s) => s.role !== 'material' && s.pages.length > 0)
  const canReorganize = aiReady && project.start_mode !== 'generate' && project.start_mode !== 'blank' && exam.blocks.length > 0
  const keyFor = (blockId: string) => exam.answer_key.find((k) => k.block_id === blockId)
  const targets = exam.blocks.filter((b) => b.kind !== 'page_break').map((b) => ({ id: b.id, label: blockName(b) }))

  const edit = (blockId: string, summary: string, kind: 'content' | 'structure' | 'layout', fn: (d: Exam) => void) =>
    apply(fn, { kind, summary, blockId })

  function focusBlock(blockId: string) {
    setSelected(blockId)
    setTimeout(() => document.getElementById(`doc-${blockId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 30)
  }

  async function runServerJob(label: string, start: () => Promise<Awaited<ReturnType<typeof api.aiOrganize>>>, success: string) {
    if (!project) return
    setAiWorking(label)
    try {
      await flushSave()
      const final = await track(await start())
      replaceProject(await api.getProject(project.id))
      if (final.status === 'done') toast.success(success)
      else toast.error(final.error ?? 'Não deu certo desta vez. Nada foi alterado.')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível falar com a IA agora.')
    } finally {
      setAiWorking(null)
    }
  }

  const redo = (block: Block, action: 'trocar' | 'mais_facil' | 'mais_dificil') =>
    void runServerJob(block.id, () => api.aiQuestion(project.id, block.id, action), {
      trocar: `${blockName(block)} trocada.`,
      mais_facil: `${blockName(block)} ficou mais fácil.`,
      mais_dificil: `${blockName(block)} ficou mais desafiadora.`,
    }[action])

  async function reorganize() {
    const ok = await confirmDialog({
      title: 'Reorganizar com IA?',
      message: 'A IA confere de novo o que é questão, alternativa e texto de apoio. Nenhuma palavra é alterada e suas correções de texto ficam.',
      confirm: 'Reorganizar',
    })
    if (ok) void runServerJob('organize', () => api.aiOrganize(project!.id), 'Organização conferida pela IA.')
  }

  async function remove(block: Block) {
    const ok = await confirmDialog({
      title: `Apagar ${blockName(block).toLowerCase()}?`,
      message: 'Você pode desfazer depois com o botão “Desfazer” no topo da página.',
      confirm: 'Apagar',
      danger: true,
    })
    if (!ok) return
    edit(block.id, `${blockName(block)} apagada`, 'structure', (d) => {
      deleteBlock(d, block.id)
      d.answer_key = d.answer_key.filter((k) => k.block_id !== block.id)
    })
    setSelected(null)
  }

  function setLines(block: Block, delta: number) {
    edit(block.id, `Linhas de resposta: ${blockName(block)}`, 'layout', (d) => {
      const target = findBlock(d, block.id)
      if (!target) return
      const space = target.answer_space
      if (space.kind !== 'lines') {
        target.answer_space = { ...space, kind: 'lines', lines: Math.max(1, delta) }
        return
      }
      const next = space.lines + delta
      target.answer_space = next < 1 ? { ...space, kind: 'none' } : { ...space, lines: Math.min(30, next) }
    })
  }

  async function insertImage(file: File) {
    const target = imageTarget.current
    if (!target || !project) return
    try {
      const res = await api.uploadAsset(project.id, file)
      edit(target.blockId, 'Imagem inserida', 'content', (d) => {
        const block = findBlock(d, target.blockId)
        if (!block) return
        const firstAlt = block.parts.findIndex((p) => p.kind === 'alternative')
        const after = firstAlt > 0 ? block.parts[firstAlt - 1].id : block.parts[block.parts.length - 1]?.id ?? null
        insertPart(d, block.id, after, makePart('image', { asset_id: res.asset_id, width_cm: res.width_cm }))
        if (target.issueId) resolveIssue(d, target.issueId)
      })
      toast.success('Imagem inserida.')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível inserir a imagem.')
    } finally {
      if (imageInput.current) imageInput.current.value = ''
    }
  }

  function askImage(blockId: string, issueId?: string) {
    imageTarget.current = { blockId, issueId }
    imageInput.current?.click()
  }

  async function finish() {
    await flushSave()
    navigate(`/prova/${project!.id}/finalizar`)
  }

  function actionsFor(block: Block, index: number) {
    const isQuestion = block.kind === 'question'
    const answer = keyFor(block.id)
    const hasOptions = block.parts.some((p) => p.kind === 'alternative')
    const showLines = isQuestion && (!hasOptions || block.answer_space.kind === 'lines')
    return (
      <div className="flex flex-col gap-3 rounded-b-xl border-t border-brand-100 bg-brand-50/60 px-3 py-3">
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
          {showLines && (
            <span className="order-first col-span-2 inline-flex items-center justify-between gap-1 rounded-xl border border-brand-200 bg-white px-1 sm:order-none sm:justify-start">
              <span className="px-2 text-sm font-bold text-slate-700">
                Linhas: {block.answer_space.kind === 'lines' ? block.answer_space.lines : 0}
              </span>
              <button
                type="button"
                className="grid size-10 place-items-center rounded-lg text-brand-800 hover:bg-brand-50"
                aria-label="Menos linhas para responder"
                onClick={() => setLines(block, -1)}
              >
                <Minus className="size-4" />
              </button>
              <button
                type="button"
                className="grid size-10 place-items-center rounded-lg text-brand-800 hover:bg-brand-50"
                aria-label="Mais linhas para responder"
                onClick={() => setLines(block, block.answer_space.kind === 'lines' ? 1 : 3)}
              >
                <Plus className="size-4" />
              </button>
            </span>
          )}
          <Action icon={<PencilLine className="size-4" aria-hidden />} onClick={() => setEditing(block.id)}>
            Editar texto
          </Action>
          <Action icon={<ImagePlus className="size-4" aria-hidden />} onClick={() => askImage(block.id)}>
            Imagem
          </Action>
          {isQuestion ? (
            <Action
              icon={<BookOpen className="size-4" aria-hidden />}
              onClick={() => edit(block.id, 'Virou texto de apoio', 'structure', (d) => changeBlockKind(d, block.id, 'support_text'))}
            >
              É texto de apoio
            </Action>
          ) : (
            <Action
              icon={<ListPlus className="size-4" aria-hidden />}
              onClick={() => edit(block.id, 'Virou questão', 'structure', (d) => changeBlockKind(d, block.id, 'question'))}
            >
              É uma questão
            </Action>
          )}
          {index > 0 && (
            <Action
              icon={<Combine className="size-4" aria-hidden />}
              onClick={() => edit(block.id, 'Juntado com o trecho anterior', 'structure', (d) => mergeWithPrevious(d, block.id))}
            >
              Juntar com o de cima
            </Action>
          )}
          <Action icon={<Trash2 className="size-4" aria-hidden />} tone="danger" onClick={() => void remove(block)}>
            Apagar
          </Action>
        </div>
        {isQuestion && aiReady && (
          <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap" aria-label="Pedir à IA">
            <Action icon={<RefreshCw className="size-4" aria-hidden />} tone="ai" disabled={!!aiWorking} onClick={() => redo(block, 'trocar')}>
              Trocar por outra
            </Action>
            <Action icon={<TrendingDown className="size-4" aria-hidden />} tone="ai" disabled={!!aiWorking} onClick={() => redo(block, 'mais_facil')}>
              Mais fácil
            </Action>
            <Action icon={<TrendingUp className="size-4" aria-hidden />} tone="ai" disabled={!!aiWorking} onClick={() => redo(block, 'mais_dificil')}>
              Mais difícil
            </Action>
          </div>
        )}
        {isQuestion && answer && (
          <div className="rounded-xl border border-emerald-200 bg-white p-3">
            <button
              type="button"
              className="flex items-center gap-2 font-bold text-emerald-800"
              aria-expanded={!!showAnswer[block.id]}
              onClick={() => setShowAnswer((s) => ({ ...s, [block.id]: !s[block.id] }))}
            >
              {showAnswer[block.id] ? <EyeOff className="size-4" aria-hidden /> : <KeyRound className="size-4" aria-hidden />}
              {showAnswer[block.id] ? 'Esconder a resposta' : 'Ver a resposta (gabarito)'}
            </button>
            {showAnswer[block.id] && (
              <div className="mt-2 flex flex-col gap-2">
                <label className="flex flex-col gap-1 text-sm font-bold text-slate-700">
                  Resposta esperada
                  <textarea
                    className={cx(inputClass, 'min-h-16 py-2 font-normal')}
                    defaultValue={answer.answer}
                    key={`a-${answer.answer}`}
                    onBlur={(e) =>
                      e.target.value !== answer.answer &&
                      edit(block.id, 'Gabarito alterado', 'content', (d) => {
                        const entry = d.answer_key.find((k) => k.block_id === block.id)
                        if (entry) entry.answer = e.target.value
                      })
                    }
                  />
                </label>
                {(answer.criteria || answer.points) && (
                  <p className="text-sm text-slate-600">
                    {answer.criteria && <>Critérios: {answer.criteria}. </>}
                    {answer.points && <>Valor: {answer.points}.</>}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  if (aiWorking && job?.status === 'running') {
    return (
      <div className="mx-auto max-w-2xl">
        <ProgressCard job={job} title={aiWorking === 'organize' ? 'Reorganizando com IA' : 'Pedindo à IA'} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-extrabold text-brand-950">Confira sua prova</h2>
          <p className="mt-1 max-w-3xl text-slate-600">
            Ela já está organizada no padrão da escola. Leia com calma e toque em uma questão para mudar algo.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {hasOriginal && (
            <Button variant="secondary" icon={showOriginal ? <EyeOff className="size-5" /> : <Eye className="size-5" />} onClick={() => setShowOriginal((v) => !v)}>
              {showOriginal ? 'Esconder o original' : 'Ver o original'}
            </Button>
          )}
          {canReorganize && (
            <Button variant="secondary" icon={<Wand2 className="size-5" />} onClick={() => void reorganize()}>
              Reorganizar com IA
            </Button>
          )}
          <Button variant="ghost" icon={<PencilLine className="size-5" />} onClick={onAdvanced}>
            Editor detalhado
          </Button>
        </div>
      </div>

      {exam.document_issues
        .filter((i) => !i.resolved && i.severity === 'info' && i.code.startsWith('ai_'))
        .map((note) => (
          <Alert
            key={note.id}
            tone="info"
            action={
              <Button size="sm" variant="secondary" onClick={() => apply((d) => resolveIssue(d, note.id), { kind: 'content', summary: 'Aviso lido' })}>
                Entendi
              </Button>
            }
          >
            {note.message}
          </Alert>
        ))}

      {points.length > 0 ? (
        <Card className="border-amber-300 p-4 sm:p-5">
          <h3 className="flex items-center gap-2 text-lg font-extrabold text-amber-900">
            <AlertTriangle className="size-5" aria-hidden /> {points.length === 1 ? '1 ponto para conferir' : `${points.length} pontos para conferir`}
          </h3>
          <ul className="mt-3 flex flex-col gap-2">
            {points.map(({ issue, block }) => {
              const doubt = issue.code === 'ai_doubt'
              const needsImage = issue.code === 'needs_image'
              return (
                <li key={issue.id} className="flex flex-col gap-2 rounded-xl border border-amber-200 bg-amber-50/60 p-3 sm:flex-row sm:items-center">
                  <div className="min-w-0 flex-1">
                    {block && <p className="text-sm font-bold text-amber-900">{blockName(block)}</p>}
                    <p className="leading-snug text-slate-800">{issue.message}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {needsImage && block && (
                      <Action icon={<ImagePlus className="size-4" aria-hidden />} onClick={() => askImage(block.id, issue.id)}>
                        Inserir imagem
                      </Action>
                    )}
                    {needsImage && block?.kind === 'question' && aiReady && (
                      <Action icon={<RefreshCw className="size-4" aria-hidden />} tone="ai" disabled={!!aiWorking} onClick={() => redo(block, 'trocar')}>
                        Trocar questão
                      </Action>
                    )}
                    <Action icon={<Check className="size-4" aria-hidden />} onClick={() => apply((d) => resolveIssue(d, issue.id), { kind: 'content', summary: 'Ponto conferido' })}>
                      {doubt ? 'Sim, está certo' : block ? 'Está certo' : 'Entendi'}
                    </Action>
                    {block && (
                      <Action icon={doubt ? <ThumbsDown className="size-4" aria-hidden /> : <ArrowRight className="size-4" aria-hidden />} onClick={() => focusBlock(block.id)}>
                        {doubt ? 'Não, corrigir' : 'Ver na prova'}
                      </Action>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </Card>
      ) : (
        <Alert tone="success" title="Nenhum ponto para conferir">
          Leia a prova uma vez. Se estiver tudo certo, siga para baixar e enviar.
        </Alert>
      )}

      <div className={cx('grid gap-5', showOriginal && 'lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]')}>
        {showOriginal && (
          <div className="lg:sticky lg:top-3 lg:h-[calc(100dvh-1.5rem)]">
            <div className="h-[70dvh] lg:h-full">
              <OriginalViewer
                project={project}
                focus={selected ? findBlock(exam, selected)?.parts[0]?.provenance ?? null : null}
                onCrop={async (sourceId, page, bbox) => {
                  try {
                    const res = await api.crop(project.id, sourceId, page, bbox)
                    const provenance = { source_id: sourceId, page, bbox, method: 'crop' as const, confidence: null }
                    const target = selected ?? exam.blocks[exam.blocks.length - 1]?.id
                    if (!target) return
                    edit(target, 'Recorte do original inserido', 'content', (d) =>
                      insertPart(d, target, findBlock(d, target)?.parts.at(-1)?.id ?? null, makePart('crop', { asset_id: res.asset_id, width_cm: res.width_cm, provenance })),
                    )
                    toast.success('Recorte inserido na questão selecionada.')
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : 'Não foi possível recortar.')
                  }
                }}
              />
            </div>
          </div>
        )}

        <div className="min-w-0">
          <article
            className="mx-auto flex max-w-[52rem] flex-col gap-1 rounded-2xl border border-slate-200 bg-white px-4 py-6 font-[Arial,Helvetica,sans-serif] text-[15px] text-ink shadow-card sm:px-10 sm:py-10"
            aria-label="Prévia da prova"
          >
            <header className="mb-4 border-b border-slate-200 pb-3 text-center">
              <p className="text-xs font-bold tracking-wide text-slate-400 uppercase">Cabeçalho da escola entra aqui automaticamente</p>
              <p className="mt-1 font-bold">{examTitle(exam.meta)}</p>
            </header>
            {exam.blocks.length === 0 && (
              <p className="py-10 text-center text-slate-500">A prova ainda está vazia. Use o editor detalhado para adicionar questões.</p>
            )}
            {exam.blocks.map((block, index) => {
              const open = selected === block.id
              const flagged = pointsToCheck({ ...exam, document_issues: [], blocks: [block] }).length > 0
              if (editing === block.id) {
                return (
                  <div key={block.id} id={`doc-${block.id}`} className="my-2 rounded-2xl border-2 border-brand-400 p-2 font-sans">
                    <div className="mb-2 flex items-center justify-between gap-2 px-1">
                      <p className="font-bold text-brand-900">Editando: {blockName(block)}</p>
                      <Button size="sm" icon={<Check className="size-4" />} onClick={() => setEditing(null)}>
                        Concluir
                      </Button>
                    </div>
                    <BlockCard block={block} index={index} total={exam.blocks.length} targets={targets} projectId={project.id} onRequestCrop={() => setShowOriginal(true)} />
                  </div>
                )
              }
              return (
                <div
                  key={block.id}
                  id={`doc-${block.id}`}
                  className={cx(
                    'group my-1 rounded-xl border-2 transition-colors',
                    open ? 'border-brand-500 shadow-sm' : flagged ? 'border-amber-300 bg-amber-50/30' : 'border-transparent hover:border-brand-200',
                    block.page_break_before && 'mt-4 border-t-slate-300',
                  )}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    aria-expanded={open}
                    aria-label={`${blockName(block)}: ${open ? 'fechar opções' : 'abrir opções'}`}
                    onClick={() => setSelected(open ? null : block.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setSelected(open ? null : block.id)
                      }
                    }}
                    className="cursor-pointer px-3 py-2"
                  >
                    {flagged && !open && (
                      <span className="float-right ml-2 inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 font-sans text-xs font-bold text-amber-900">
                        <FileWarning className="size-3.5" aria-hidden /> Conferir
                      </span>
                    )}
                    {block.kind === 'question' && aiWorking === block.id && (
                      <span className="float-right ml-2 inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 font-sans text-xs font-bold text-violet-800">
                        <Sparkles className="size-3.5" aria-hidden /> IA trabalhando…
                      </span>
                    )}
                    <BlockView block={block} projectId={project.id} />
                  </div>
                  {open && (
                    <div className="font-sans">
                      <div className="flex items-center justify-between px-3 pb-1">
                        <span className="text-sm font-bold text-brand-800">{blockName(block)}</span>
                        <button type="button" className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100" aria-label="Fechar opções" onClick={() => setSelected(null)}>
                          <X className="size-4" />
                        </button>
                      </div>
                      {actionsFor(block, index)}
                    </div>
                  )}
                </div>
              )
            })}
            {exam.meta.mensagem_final && <p className="mt-4 text-center font-bold">{exam.meta.mensagem_final}</p>}
          </article>

          <div className="mx-auto mt-5 flex max-w-[52rem] flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
            <p className="flex items-center gap-2 text-slate-600">
              <CheckCircle2 className="size-5 text-emerald-600" aria-hidden /> Tudo certo? A diagramação é automática.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => void flushSave().then(() => navigate(`/prova/${project.id}/ajustar`))}>
                Ajustar impressão (opcional)
              </Button>
              <Button size="lg" icon={<ArrowRight className="size-5" />} onClick={() => void finish()}>
                Baixar e enviar
              </Button>
            </div>
          </div>
        </div>
      </div>

      <input
        ref={imageInput}
        type="file"
        accept="image/png,image/jpeg"
        className="sr-only"
        aria-label="Escolher imagem"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void insertImage(file)
        }}
      />
    </div>
  )
}
