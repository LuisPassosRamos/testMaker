import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Combine,
  Crop,
  Eye,
  FileWarning,
  ImagePlus,
  Info,
  ListChecks,
  Pilcrow,
  Plus,
  RotateCcw,
  Ruler,
  Scissors,
  Trash2,
  UserPen,
  XCircle,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, urls } from '../../api/client'
import type { Block, BlockKind, Issue, Part } from '../../api/types'
import { confirmDialog } from '../../components/Dialog'
import { toast } from '../../components/Toast'
import { Badge, Button, IconButton, Select, Toggle, cx, inputBase } from '../../components/ui'
import {
  changeBlockKind,
  deleteBlock,
  deletePart,
  findBlock,
  findPart,
  hasLayoutChange,
  insertPart,
  isEdited,
  makePart,
  mergeWithPrevious,
  moveBlock,
  movePart,
  movePartToBlock,
  openIssues,
  resolveIssue,
  restoreOriginal,
  setPartKind,
  setPartRuns,
  splitBlockAt,
} from '../../lib/exam-ops'
import { BLOCK_KINDS } from '../../lib/labels'
import { runsEqual, runsText } from '../../lib/runs'
import { useExamStore } from '../../store/examStore'
import { AnswerSpaceEditor } from './AnswerSpaceEditor'
import { RichTextEditor } from './RichTextEditor'
import { RunsView } from './RunsView'

const ISSUE_STYLE = {
  error: { box: 'border-rose-300 bg-rose-50 text-rose-950', icon: <XCircle className="size-4 shrink-0 text-rose-700" aria-hidden /> },
  warning: { box: 'border-amber-300 bg-amber-50 text-amber-950', icon: <AlertTriangle className="size-4 shrink-0 text-amber-700" aria-hidden /> },
  info: { box: 'border-slate-200 bg-slate-50 text-slate-800', icon: <Info className="size-4 shrink-0 text-slate-500" aria-hidden /> },
}

/** Slider that records a single undo step when released. */
function WidthSlider({ value, onCommit }: { value: number; onCommit: (value: number) => void }) {
  const [local, setLocal] = useState(value)
  useEffect(() => setLocal(value), [value])
  const commit = () => local !== value && onCommit(local)
  return (
    <label className="flex flex-col gap-1 text-sm font-bold text-slate-700">
      Tamanho na impressão: {local}%
      <input
        type="range"
        min={20}
        max={100}
        step={5}
        value={local}
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
        className="accent-brand-700"
      />
    </label>
  )
}

export function IssueNote({ issue, onResolve }: { issue: Issue; onResolve: () => void }) {
  const style = ISSUE_STYLE[issue.severity]
  return (
    <div className={cx('flex items-start gap-2 rounded-lg border px-3 py-2 text-sm', style.box)}>
      <span className="mt-0.5">{style.icon}</span>
      <p className="flex-1 leading-snug">{issue.message}</p>
      <button className="shrink-0 rounded-md px-2 py-0.5 font-bold underline-offset-2 hover:underline" onClick={onResolve}>
        Conferido
      </button>
    </div>
  )
}

interface BlockCardProps {
  block: Block
  index: number
  total: number
  targets: { id: string; label: string }[]
  projectId: string
  onRequestCrop: (partId: string) => void
}

export function BlockCard({ block, index, total, targets, projectId, onRequestCrop }: BlockCardProps) {
  const { apply, select, selectedPartId } = useExamStore()
  const [editing, setEditing] = useState<string | null>(null)
  const [showOriginal, setShowOriginal] = useState<Record<string, boolean>>({})
  const fileInput = useRef<HTMLInputElement>(null)
  const issues = openIssues(block)
  const edited = block.parts.filter(isEdited).length
  const isQuestion = block.kind === 'question'
  const title = isQuestion ? `Questão ${block.number || 'sem número'}` : BLOCK_KINDS[block.kind].label

  const edit = (summary: string, kind: 'content' | 'structure' | 'layout', fn: (draft: Parameters<Parameters<typeof apply>[0]>[0]) => void) =>
    apply(fn, { kind, summary, blockId: block.id })

  async function addImage(file: File) {
    try {
      const res = await api.uploadAsset(projectId, file)
      edit(`Imagem adicionada em “${title}”`, 'content', (d) =>
        insertPart(d, block.id, block.parts[block.parts.length - 1]?.id ?? null, makePart('image', { asset_id: res.asset_id, width_cm: res.width_cm })),
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Não foi possível adicionar a imagem.')
    }
  }

  if (block.kind === 'page_break') {
    return (
      <div id={`bloco-${block.id}`} className="flex items-center gap-3 rounded-xl border-2 border-dashed border-teal-300 bg-teal-50 px-4 py-2 text-teal-900">
        <Ruler className="size-5" aria-hidden />
        <span className="flex-1 font-bold">Quebra de página — o próximo conteúdo começa em uma nova folha</span>
        <IconButton label="Remover quebra de página" onClick={() => edit('Quebra de página removida', 'layout', (d) => deleteBlock(d, block.id))}>
          <Trash2 className="size-5" />
        </IconButton>
      </div>
    )
  }

  const partRow = (part: Part, pIndex: number) => {
    const selected = selectedPartId === part.id
    const partIssues = part.issues.filter((i) => !i.resolved)
    const weakWords = partIssues.flatMap((i) => (Array.isArray(i.detail?.words) ? (i.detail!.words as string[]) : []))
    const textual = part.kind === 'paragraph' || part.kind === 'alternative'
    const changed = isEdited(part)
    return (
      <li
        key={part.id}
        className={cx(
          'group relative rounded-xl border px-3 py-2 transition-colors',
          selected ? 'border-brand-400 bg-brand-50/60 ring-2 ring-brand-200' : 'border-transparent hover:border-slate-200 hover:bg-slate-50',
          part.kind === 'alternative' && 'ml-6',
        )}
        onClick={() => select(part.id)}
      >
        {textual && (
          <div className="flex items-start gap-2">
            {part.kind === 'alternative' && (
              <input
                aria-label="Rótulo da alternativa"
                className={`${inputBase} min-h-9 w-16 px-2 text-center font-bold`}
                defaultValue={part.label ?? ''}
                key={`${part.id}-${part.label}`}
                onBlur={(e) => {
                  const value = e.target.value.trim()
                  if (value !== (part.label ?? '')) edit(`Rótulo da alternativa alterado para “${value}”`, 'content', (d) => {
                    const f = findPart(d, part.id)
                    if (f) f.part.label = value
                  })
                }}
              />
            )}
            <div className="min-w-0 flex-1">
              {editing === part.id ? (
                <RichTextEditor
                  runs={part.runs}
                  label={part.kind === 'alternative' ? `Texto da alternativa ${part.label ?? ''}` : 'Texto do trecho'}
                  onCancel={() => setEditing(null)}
                  onCommit={(runs) => {
                    setEditing(null)
                    if (!runsEqual(runs, part.runs)) {
                      edit(`Texto corrigido em “${title}”`, 'content', (d) => setPartRuns(d, part.id, runs))
                    }
                  }}
                />
              ) : (
                <div
                  role="button"
                  tabIndex={0}
                  aria-label={`Editar: ${runsText(part.runs).slice(0, 80) || 'trecho vazio'}`}
                  onClick={() => setEditing(part.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setEditing(part.id)
                    }
                  }}
                  className={cx(
                    'cursor-text rounded-lg px-1 py-1 leading-relaxed text-ink [overflow-wrap:anywhere]',
                    part.kind === 'alternative' && 'pt-1.5',
                    part.align === 'center' && 'text-center',
                    !runsText(part.runs).trim() && 'italic text-slate-400',
                  )}
                >
                  {runsText(part.runs).trim() ? <RunsView runs={part.runs} highlight={weakWords} /> : 'Clique para escrever…'}
                </div>
              )}
            </div>
          </div>
        )}

        {(part.kind === 'image' || part.kind === 'crop') && (
          <div className="flex flex-wrap items-start gap-4">
            {part.asset_id ? (
              <img
                src={urls.asset(projectId, part.asset_id)}
                alt={part.kind === 'crop' ? 'Recorte do original' : 'Imagem da questão'}
                className="max-h-56 rounded-lg border border-slate-200 bg-white object-contain"
                style={{ width: `${Math.max(25, part.width_pct)}%`, maxWidth: '100%' }}
              />
            ) : (
              <div className="flex flex-1 items-center gap-3 rounded-lg border-2 border-dashed border-rose-300 bg-rose-50 p-4 text-rose-900">
                <FileWarning className="size-6 shrink-0" aria-hidden />
                <p className="flex-1 text-sm">Este elemento não pôde ser convertido. Recorte-o da imagem do original para preservá-lo.</p>
                <Button size="sm" icon={<Crop className="size-4" />} onClick={() => onRequestCrop(part.id)}>
                  Recortar do original
                </Button>
              </div>
            )}
            {part.asset_id && (
              <div className="flex min-w-56 flex-1 flex-col gap-3">
                <WidthSlider
                  value={part.width_pct}
                  onCommit={(value) =>
                    edit(`Tamanho da imagem: ${value}%`, 'layout', (d) => {
                      const f = findPart(d, part.id)
                      if (f) f.part.width_pct = value
                    })
                  }
                />
                <label className="flex flex-col gap-1 text-sm font-bold text-slate-700">
                  Pertence a
                  <Select
                    value={block.id}
                    onChange={(e) => {
                      const target = targets.find((t) => t.id === e.target.value)
                      edit(`Imagem movida para “${target?.label}”`, 'structure', (d) => movePartToBlock(d, part.id, e.target.value))
                    }}
                  >
                    {targets.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.label}
                      </option>
                    ))}
                  </Select>
                </label>
              </div>
            )}
          </div>
        )}

        {part.kind === 'table' && part.rows && (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <tbody>
                {part.rows.map((row, r) => (
                  <tr key={r}>
                    {row
                      .filter((c) => c.vmerge !== 'continue')
                      .map((cell, c) => (
                        <td key={c} colSpan={cell.colspan} className="border border-slate-300 px-2 py-1 align-top">
                          {cell.content.map((content, k) =>
                            content.asset_id ? (
                              <img key={k} src={urls.asset(projectId, content.asset_id)} alt="" className="max-h-24" />
                            ) : (
                              <p key={k}>
                                <RunsView runs={content.runs} />
                              </p>
                            ),
                          )}
                        </td>
                      ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-xs text-slate-500">Tabela preservada como no original. Se algo estiver errado, use “Recortar do original”.</p>
          </div>
        )}

        {part.kind === 'answer_space' && part.answer && (
          <AnswerSpaceEditor
            label="Espaço de resposta aqui"
            allowNone={false}
            value={part.answer}
            onChange={(answer) =>
              edit('Espaço de resposta ajustado', 'layout', (d) => {
                const f = findPart(d, part.id)
                if (f) f.part.answer = answer
              })
            }
          />
        )}

        {(partIssues.length > 0 || changed) && (
          <div className="mt-2 flex flex-col gap-2">
            {partIssues.map((issue) => (
              <IssueNote key={issue.id} issue={issue} onResolve={() => apply((d) => resolveIssue(d, issue.id))} />
            ))}
            {changed && (
              <div className="rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-sm text-violet-950">
                <div className="flex flex-wrap items-center gap-2">
                  <UserPen className="size-4 text-violet-700" aria-hidden />
                  <span className="font-bold">Corrigido por você</span>
                  <button className="ml-auto inline-flex items-center gap-1 font-bold underline-offset-2 hover:underline" onClick={() => setShowOriginal((s) => ({ ...s, [part.id]: !s[part.id] }))}>
                    <Eye className="size-4" aria-hidden /> {showOriginal[part.id] ? 'Esconder' : 'Ver'} texto extraído
                  </button>
                  <button
                    className="inline-flex items-center gap-1 font-bold underline-offset-2 hover:underline"
                    onClick={() => edit(`Correção desfeita em “${title}”`, 'content', (d) => restoreOriginal(d, part.id))}
                  >
                    <RotateCcw className="size-4" aria-hidden /> Voltar ao extraído
                  </button>
                </div>
                {showOriginal[part.id] && (
                  <p className="mt-2 rounded bg-white/70 p-2 text-ink">
                    {part.original_label && <strong>{part.original_label} </strong>}
                    <RunsView runs={part.original_runs ?? []} />
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        <div
          className={cx(
            'z-10 flex-wrap items-center gap-0.5 rounded-xl border border-slate-200 bg-white p-0.5 shadow-card',
            'mt-1 sm:absolute sm:-top-4 sm:right-2 sm:mt-0',
            editing === part.id ? 'hidden' : selected ? 'flex' : 'hidden group-focus-within:flex sm:group-hover:flex',
          )}
          role="toolbar"
          aria-label="Ações do trecho"
        >
          <IconButton label="Mover trecho para cima" disabled={pIndex === 0} onClick={() => edit('Trecho movido', 'structure', (d) => movePart(d, part.id, -1))}>
            <ArrowUp className="size-4" />
          </IconButton>
          <IconButton
            label="Mover trecho para baixo"
            disabled={pIndex === block.parts.length - 1}
            onClick={() => edit('Trecho movido', 'structure', (d) => movePart(d, part.id, 1))}
          >
            <ArrowDown className="size-4" />
          </IconButton>
          {textual && isQuestion && (
            <IconButton
              label={part.kind === 'alternative' ? 'Transformar em parte do enunciado' : 'Transformar em alternativa'}
              onClick={() =>
                edit(part.kind === 'alternative' ? 'Alternativa virou enunciado' : 'Parágrafo virou alternativa', 'structure', (d) =>
                  setPartKind(d, part.id, part.kind === 'alternative' ? 'paragraph' : 'alternative'),
                )
              }
            >
              {part.kind === 'alternative' ? <Pilcrow className="size-4" /> : <ListChecks className="size-4" />}
            </IconButton>
          )}
          {(pIndex > 0 || !isQuestion) && (
            <IconButton
              label="Começar uma nova questão a partir deste trecho"
              onClick={() => edit('Bloco separado em nova questão', 'structure', (d) => splitBlockAt(d, part.id, 'question'))}
            >
              <Scissors className="size-4" />
            </IconButton>
          )}
          {pIndex > 0 && (
            <IconButton
              label="Separar como texto de apoio a partir daqui"
              onClick={() => edit('Trecho separado como texto de apoio', 'structure', (d) => splitBlockAt(d, part.id, 'support_text'))}
            >
              <Combine className="size-4 rotate-90" />
            </IconButton>
          )}
          <IconButton
            label="Excluir trecho"
            className="hover:bg-rose-50 hover:text-rose-700"
            onClick={async () => {
              const ok = await confirmDialog({
                title: 'Excluir este trecho?',
                message: 'O trecho sairá da prova. Você pode desfazer com Ctrl+Z ou pelo botão Desfazer.',
                confirm: 'Excluir',
                danger: true,
              })
              if (ok) edit(`Trecho excluído de “${title}”`, 'structure', (d) => deletePart(d, part.id))
            }}
          >
            <Trash2 className="size-4" />
          </IconButton>
        </div>
      </li>
    )
  }

  return (
    <article
      id={`bloco-${block.id}`}
      aria-label={title}
      className={cx(
        'scroll-mt-28 rounded-2xl border bg-white shadow-card',
        issues.some((i) => i.severity === 'error') ? 'border-rose-300' : issues.length ? 'border-amber-300' : block.reviewed ? 'border-emerald-300' : 'border-slate-200',
      )}
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
        {isQuestion ? (
          <label className="flex items-center gap-2">
            <span className="font-display text-lg font-extrabold text-brand-950">Questão</span>
            <input
              aria-label="Número da questão como aparece na prova"
              className={`${inputBase} min-h-9 w-24 px-2 font-bold`}
              defaultValue={block.number ?? ''}
              key={`${block.id}-${block.number}`}
              onBlur={(e) => {
                const value = e.target.value.trim()
                if (value !== (block.number ?? '')) edit(`Número da questão alterado para “${value}”`, 'content', (d) => {
                  const b = findBlock(d, block.id)
                  if (b) b.number = value
                })
              }}
            />
          </label>
        ) : (
          <span className="font-display text-lg font-extrabold text-brand-950">{title}</span>
        )}
        <div className="flex flex-wrap gap-1.5">
          {issues.length > 0 && (
            <Badge tone={issues.some((i) => i.severity === 'error') ? 'rose' : 'amber'} icon={<AlertTriangle className="size-3.5" aria-hidden />}>
              {issues.length} para revisar
            </Badge>
          )}
          {edited > 0 && (
            <Badge tone="violet" icon={<UserPen className="size-3.5" aria-hidden />}>
              {edited} corrigido{edited > 1 ? 's' : ''}
            </Badge>
          )}
          {hasLayoutChange(block) && (
            <Badge tone="teal" icon={<Ruler className="size-3.5" aria-hidden />}>
              Diagramação
            </Badge>
          )}
          {block.reviewed && issues.length === 0 && (
            <Badge tone="emerald" icon={<CheckCircle2 className="size-3.5" aria-hidden />}>
              Conferido
            </Badge>
          )}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          <select
            aria-label="Tipo do bloco"
            className="min-h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm font-bold text-slate-700 hover:border-brand-300"
            value={block.kind}
            onChange={(e) =>
              edit(`Bloco transformado em “${BLOCK_KINDS[e.target.value as BlockKind].label}”`, 'structure', (d) =>
                changeBlockKind(d, block.id, e.target.value as BlockKind),
              )
            }
          >
            {(['question', 'support_text', 'instructions', 'original_header', 'free'] as BlockKind[]).map((k) => (
              <option key={k} value={k}>
                {BLOCK_KINDS[k].label}
              </option>
            ))}
          </select>
          <IconButton label="Mover bloco para cima" disabled={index === 0} onClick={() => edit(`“${title}” movido para cima`, 'structure', (d) => moveBlock(d, block.id, -1))}>
            <ArrowUp className="size-5" />
          </IconButton>
          <IconButton label="Mover bloco para baixo" disabled={index === total - 1} onClick={() => edit(`“${title}” movido para baixo`, 'structure', (d) => moveBlock(d, block.id, 1))}>
            <ArrowDown className="size-5" />
          </IconButton>
          <IconButton label="Juntar com o bloco anterior" disabled={index === 0} onClick={() => edit(`“${title}” juntado ao bloco anterior`, 'structure', (d) => mergeWithPrevious(d, block.id))}>
            <Combine className="size-5" />
          </IconButton>
          <IconButton
            label="Excluir bloco"
            className="hover:bg-rose-50 hover:text-rose-700"
            onClick={async () => {
              const ok = await confirmDialog({
                title: `Excluir “${title}”?`,
                message: 'Todo o conteúdo deste bloco sairá da prova. Você pode desfazer depois.',
                confirm: 'Excluir bloco',
                danger: true,
              })
              if (ok) edit(`“${title}” excluído`, 'structure', (d) => deleteBlock(d, block.id))
            }}
          >
            <Trash2 className="size-5" />
          </IconButton>
        </div>
      </header>

      <div className="flex flex-col gap-2 px-3 py-3">
        {block.issues.filter((i) => !i.resolved).map((issue) => (
          <IssueNote key={issue.id} issue={issue} onResolve={() => apply((d) => resolveIssue(d, issue.id))} />
        ))}
        {block.kind === 'original_header' && (
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
            Por padrão, este cabeçalho é trocado pelo modelo da escola na impressão (dá para mudar no ajuste de impressão).
          </p>
        )}
        {block.parts.length === 0 && <p className="px-2 text-slate-500 italic">Bloco vazio — adicione conteúdo abaixo.</p>}
        <ul className="flex flex-col gap-1">{block.parts.map(partRow)}</ul>

        <div className="flex flex-wrap gap-2 px-1 pt-1">
          <Button
            size="sm"
            variant="ghost"
            icon={<Plus className="size-4" />}
            onClick={() => {
              const part = makePart('paragraph')
              edit('Parágrafo adicionado', 'content', (d) => insertPart(d, block.id, block.parts[block.parts.length - 1]?.id ?? null, part))
              setEditing(part.id)
            }}
          >
            Parágrafo
          </Button>
          {isQuestion && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Plus className="size-4" />}
              onClick={() => {
                const letters = block.parts.filter((p) => p.kind === 'alternative').length
                const part = makePart('alternative', { label: `${String.fromCharCode(97 + letters)})` })
                edit('Alternativa adicionada', 'content', (d) => insertPart(d, block.id, block.parts[block.parts.length - 1]?.id ?? null, part))
                setEditing(part.id)
              }}
            >
              Alternativa
            </Button>
          )}
          <Button size="sm" variant="ghost" icon={<ImagePlus className="size-4" />} onClick={() => fileInput.current?.click()}>
            Imagem
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="sr-only"
            aria-label="Escolher imagem para adicionar"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void addImage(file)
              e.target.value = ''
            }}
          />
        </div>
      </div>

      {block.kind !== 'original_header' && (
        <footer className="flex flex-col gap-4 rounded-b-2xl border-t border-slate-100 bg-slate-50/70 px-4 py-3">
          {isQuestion && (
            <AnswerSpaceEditor
              value={block.answer_space}
              onChange={(answer) =>
                edit('Espaço de resposta ajustado', 'layout', (d) => {
                  const b = findBlock(d, block.id)
                  if (b) b.answer_space = answer
                })
              }
            />
          )}
          <div className="flex flex-wrap gap-x-8 gap-y-3">
            <Toggle
              label="Começar em nova página"
              checked={block.page_break_before}
              onChange={(v) =>
                edit(v ? 'Quebra de página inserida antes do bloco' : 'Quebra de página removida', 'layout', (d) => {
                  const b = findBlock(d, block.id)
                  if (b) b.page_break_before = v
                })
              }
            />
            {isQuestion && block.parts.some((p) => p.kind === 'alternative') && (
              <Toggle
                label="Alternativas lado a lado"
                description="Economiza espaço quando são curtas."
                checked={block.alternatives_inline}
                onChange={(v) =>
                  edit('Disposição das alternativas alterada', 'layout', (d) => {
                    const b = findBlock(d, block.id)
                    if (b) b.alternatives_inline = v
                  })
                }
              />
            )}
            {isQuestion && (
              <Toggle
                label="Pode dividir entre páginas"
                description="Para questões maiores que uma folha."
                checked={block.allow_split}
                onChange={(v) =>
                  edit('Divisão entre páginas alterada', 'layout', (d) => {
                    const b = findBlock(d, block.id)
                    if (b) b.allow_split = v
                  })
                }
              />
            )}
          </div>
          <div>
            <Button
              size="sm"
              variant={block.reviewed && issues.length === 0 ? 'success' : 'secondary'}
              icon={<CheckCircle2 className="size-4" />}
              onClick={() =>
                apply((d) => {
                  const b = findBlock(d, block.id)
                  if (!b) return
                  const value = !(block.reviewed && issues.length === 0)
                  b.reviewed = value
                  if (value) openIssues(b).forEach((i) => (i.resolved = true))
                })
              }
            >
              {block.reviewed && issues.length === 0 ? 'Conferido' : issues.length ? 'Conferi este bloco (marca os avisos como vistos)' : 'Marcar como conferido'}
            </Button>
          </div>
        </footer>
      )}
    </article>
  )
}
