import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ArrowDown, ArrowUp, ClipboardPaste, FileImage, FileText, FileUp, GripVertical, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../../api/client'
import type { Run, SourceFile } from '../../api/types'
import { confirmDialog } from '../../components/Dialog'
import { ProgressCard } from '../../components/ProgressCard'
import { toast } from '../../components/Toast'
import { Alert, Button, Card, IconButton, Toggle, cx } from '../../components/ui'
import { useJob } from '../../hooks/useJob'
import { useSystem } from '../../hooks/useHealth'
import { htmlToParagraphs, textToParagraphs } from '../../lib/runs'
import { useExamStore } from '../../store/examStore'
import { RunsView } from '../review/RunsView'

const previews = new Map<string, string>() // source id -> object URL (this session only)

function formatSize(bytes: number) {
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function SortableImage({ source, index, total, onMove, onRemove }: {
  source: SourceFile
  index: number
  total: number
  onMove: (delta: number) => void
  onRemove: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: source.id })
  const url = previews.get(source.id)
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cx('flex flex-col overflow-hidden rounded-2xl border-2 bg-white', isDragging ? 'z-10 border-brand-500 shadow-lift' : 'border-slate-200')}
    >
      <div className="flex items-center justify-between bg-brand-50 px-3 py-2">
        <span className="font-display font-extrabold text-brand-900">Página {index + 1}</span>
        <button
          className="cursor-grab rounded-lg p-1.5 text-brand-700 hover:bg-white active:cursor-grabbing"
          aria-label={`Arrastar a página ${index + 1} para mudar a ordem`}
          {...attributes}
          {...listeners}
        >
          <GripVertical className="size-5" />
        </button>
      </div>
      <div className="grid aspect-[3/4] place-items-center bg-slate-100">
        {url ? <img src={url} alt={`Página ${index + 1}: ${source.name}`} className="h-full w-full object-contain" /> : <FileImage className="size-12 text-slate-400" aria-hidden />}
      </div>
      <div className="flex items-center gap-1 px-2 py-2">
        <span className="min-w-0 flex-1 truncate px-1 text-sm text-slate-600" title={source.name}>
          {source.name}
        </span>
        <IconButton label="Mover para antes" disabled={index === 0} onClick={() => onMove(-1)}>
          <ArrowUp className="size-4" />
        </IconButton>
        <IconButton label="Mover para depois" disabled={index === total - 1} onClick={() => onMove(1)}>
          <ArrowDown className="size-4" />
        </IconButton>
        <IconButton label={`Remover ${source.name}`} onClick={onRemove} className="hover:bg-rose-50 hover:text-rose-700">
          <Trash2 className="size-4" />
        </IconButton>
      </div>
    </li>
  )
}

function PasteBox({ paragraphs, onChange }: { paragraphs: Run[][]; onChange: (p: Run[][]) => void }) {
  return (
    <div className="flex flex-col gap-3">
      <div
        role="textbox"
        aria-multiline="true"
        aria-label="Área para colar o texto da prova"
        tabIndex={0}
        contentEditable
        suppressContentEditableWarning
        onPaste={(e) => {
          e.preventDefault()
          const html = e.clipboardData.getData('text/html')
          const text = e.clipboardData.getData('text/plain')
          const parsed = html ? htmlToParagraphs(html) : textToParagraphs(text)
          onChange([...paragraphs, ...parsed])
        }}
        onKeyDown={(e) => {
          if (!(e.ctrlKey || e.metaKey) && e.key.length === 1) e.preventDefault()
        }}
        className="min-h-40 rounded-2xl border-2 border-dashed border-brand-300 bg-brand-50/50 p-5 text-slate-600 focus:border-brand-600 focus:bg-white focus:outline-none"
      >
        {paragraphs.length === 0 ? (
          <span className="pointer-events-none select-none">
            Clique aqui e cole (Ctrl+V) o texto copiado do Word, Google Docs ou de outro lugar. Negrito, itálico e sublinhado são mantidos.
          </span>
        ) : (
          <div className="pointer-events-none space-y-1 text-ink select-none" aria-hidden>
            {paragraphs.map((p, i) => (p.length ? <p key={i}><RunsView runs={p} /></p> : <br key={i} />))}
          </div>
        )}
      </div>
      {paragraphs.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 text-sm text-slate-600">
          <span>{paragraphs.filter((p) => p.length).length} parágrafos colados.</span>
          <Button size="sm" variant="ghost" onClick={() => onChange([])}>
            Limpar e colar de novo
          </Button>
        </div>
      )}
    </div>
  )
}

export default function UploadStep() {
  const { project, exam, replaceProject } = useExamStore()
  const navigate = useNavigate()
  const health = useSystem((s) => s.health)
  const { job, track } = useJob(project?.id)
  const [mode, setMode] = useState<'upload' | 'paste'>(project?.start_mode === 'paste' ? 'paste' : 'upload')
  const [rejected, setRejected] = useState<{ name: string; message: string }[]>([])
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [pasted, setPasted] = useState<Run[][]>([])
  const [reading, setReading] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))

  useEffect(() => {
    if (project?.job?.status === 'running' && project.job.kind === 'extract') {
      setReading(true)
      void track(project.job).then(onExtractionFinished)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!project || !exam) return null
  const sources = project.sources.filter((s) => s.role !== 'material')
  const images = sources.every((s) => s.kind === 'image') && sources.length > 0
  const aiReading = Boolean(health?.ai.enabled && health.ai.reading)

  async function toggleAi(value: boolean) {
    if (!project) return
    try {
      replaceProject(await api.setUseAi(project.id, value))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível mudar a opção.')
    }
  }

  async function onExtractionFinished(final: { status: string }) {
    if (!project) return
    const fresh = await api.getProject(project.id)
    replaceProject(fresh)
    setReading(false)
    if (final.status === 'done') {
      toast.success('Leitura concluída! Agora confira o conteúdo.')
      navigate(`/prova/${project.id}/conferir`)
    }
  }

  async function upload(files: File[]) {
    if (!files.length || !project) return
    setUploading(true)
    setRejected([])
    try {
      const before = new Set(project.sources.map((s) => s.id))
      const res = await api.uploadSources(project.id, files)
      const added = res.project.sources.filter((s) => !before.has(s.id))
      const accepted = files.filter((f) => !res.rejected.some((r) => r.name === f.name))
      added.forEach((s, i) => {
        if (s.kind === 'image' && accepted[i]) previews.set(s.id, URL.createObjectURL(accepted[i]))
      })
      replaceProject(res.project)
      setRejected(res.rejected)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível enviar os arquivos.')
    } finally {
      setUploading(false)
      if (input.current) input.current.value = ''
    }
  }

  async function reorder(ids: string[]) {
    if (!project) return
    try {
      replaceProject(await api.orderSources(project.id, ids))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Erro ao reordenar.')
    }
  }

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const ids = sources.map((s) => s.id)
    void reorder(arrayMove(ids, ids.indexOf(String(active.id)), ids.indexOf(String(over.id))))
  }

  async function remove(source: SourceFile) {
    if (!project) return
    replaceProject(await api.deleteSource(project.id, source.id))
  }

  async function read() {
    if (!project || !exam) return
    let replace = false
    if (exam.blocks.length) {
      replace = await confirmDialog({
        title: 'Ler os arquivos de novo?',
        message: 'O conteúdo que já está na conferência será substituído pela nova leitura. Suas correções serão perdidas.',
        confirm: 'Ler de novo',
        danger: true,
      })
      if (!replace) return
    }
    setReading(true)
    try {
      const started = await api.extract(project.id, replace)
      const final = await track(started)
      await onExtractionFinished(final)
    } catch (err) {
      setReading(false)
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível iniciar a leitura.')
    }
  }

  async function organizePasted() {
    if (!project || !exam) return
    let replace = false
    if (exam.blocks.length) {
      replace = await confirmDialog({
        title: 'Substituir o conteúdo atual?',
        message: 'Escolha “Substituir” para trocar o conteúdo pelo texto colado, ou “Cancelar” para manter.',
        confirm: 'Substituir',
        danger: true,
      })
      if (!replace) return
    }
    setReading(true)
    try {
      replaceProject(await api.paste(project.id, pasted.filter((p, i, all) => p.length || (i > 0 && all[i - 1].length)), replace))
      if (aiReading && project.use_ai) {
        // The rules already organised the text; the AI checks and fixes the organisation (never the words).
        try {
          const final = await track(await api.aiOrganize(project.id))
          replaceProject(await api.getProject(project.id))
          if (final.status === 'failed') toast.error('A IA não conseguiu conferir agora; foi usada a organização automática simples.')
        } catch {
          /* the rule-based organisation is already saved */
        }
      }
      navigate(`/prova/${project.id}/conferir`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível organizar o texto.')
    } finally {
      setReading(false)
    }
  }

  const aiSwitch = aiReading ? (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <Toggle
        label="Organizar com inteligência artificial (recomendado)"
        description={`A IA separa questões, alternativas e textos de apoio sem mudar nenhuma palavra. ${health?.ai.notice ?? ''}`}
        checked={project.use_ai}
        onChange={(v) => void toggleAi(v)}
      />
    </div>
  ) : null

  if (reading && job && job.status === 'running') {
    return (
      <div className="mx-auto max-w-2xl">
        <ProgressCard job={job} title={job.kind === 'organize' ? 'Organizando a prova' : 'Lendo a prova'} />
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-extrabold text-brand-950">{mode === 'upload' ? 'Envie o arquivo da prova' : 'Cole o texto da prova'}</h2>
          <p className="mt-1 text-slate-600">
            {mode === 'upload'
              ? 'Depois vamos ler o conteúdo e organizar as questões. Nada é reescrito.'
              : 'Vamos separar as questões e alternativas automaticamente. Você confere em seguida.'}
          </p>
        </div>
        <Button variant="secondary" icon={mode === 'upload' ? <ClipboardPaste className="size-5" /> : <FileUp className="size-5" />} onClick={() => setMode(mode === 'upload' ? 'paste' : 'upload')}>
          {mode === 'upload' ? 'Prefiro colar o texto' : 'Prefiro enviar um arquivo'}
        </Button>
      </div>

      {job?.status === 'failed' && <ProgressCard job={job} title="Leitura" />}
      {aiSwitch}

      {mode === 'upload' ? (
        <>
          <Card className="p-5 sm:p-6">
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                void upload(Array.from(e.dataTransfer.files))
              }}
              className={cx(
                'flex flex-col items-center gap-4 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors',
                dragging ? 'border-brand-600 bg-brand-50' : 'border-brand-300 bg-brand-50/40',
              )}
            >
              <span className="grid size-16 place-items-center rounded-2xl bg-brand-700 text-white shadow-sm" aria-hidden>
                <FileUp className="size-8" />
              </span>
              <div>
                <p className="text-xl font-extrabold text-brand-950">Arraste o arquivo para cá</p>
                <p className="text-slate-600">ou</p>
              </div>
              <Button size="lg" onClick={() => input.current?.click()} loading={uploading}>
                Escolher arquivo no computador
              </Button>
              <input
                ref={input}
                type="file"
                className="sr-only"
                multiple
                accept=".docx,.pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/*,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(e) => void upload(Array.from(e.target.files ?? []))}
                aria-label="Escolher arquivo da prova"
              />
              <ul className="flex flex-wrap justify-center gap-2 text-sm">
                <li className="rounded-full bg-white px-3 py-1 font-bold text-brand-800 shadow-sm">Word (.docx) — melhor resultado</li>
                <li className="rounded-full bg-white px-3 py-1 font-bold text-brand-800 shadow-sm">PDF</li>
                <li className="rounded-full bg-white px-3 py-1 font-bold text-brand-800 shadow-sm">Fotos JPG/PNG — uma por página</li>
              </ul>
              {health && (
                <p className="text-sm text-slate-500">Até {health.limits.max_upload_mb} MB por arquivo. Arquivos .doc antigos: abra no Word e salve como .docx.</p>
              )}
            </div>
          </Card>

          {rejected.map((r) => (
            <Alert key={r.name + r.message} tone="warning" title={`Não foi possível usar “${r.name}”`}>
              {r.message}
            </Alert>
          ))}

          {sources.length > 0 && (
            <Card className="p-5 sm:p-6">
              <h3 className="text-lg font-extrabold text-brand-950">
                {images ? `Páginas da prova (${sources.length})` : 'Arquivo enviado'}
              </h3>
              {images && (
                <p className="mt-1 text-slate-600">
                  Confira a ordem das páginas: arraste ou use as setas. Todas as fotos serão tratadas como páginas de uma mesma prova.
                </p>
              )}
              {images ? (
                <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
                  <SortableContext items={sources.map((s) => s.id)} strategy={rectSortingStrategy}>
                    <ul className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
                      {sources.map((s, i) => (
                        <SortableImage
                          key={s.id}
                          source={s}
                          index={i}
                          total={sources.length}
                          onRemove={() => void remove(s)}
                          onMove={(delta) => {
                            const ids = sources.map((x) => x.id)
                            void reorder(arrayMove(ids, i, i + delta))
                          }}
                        />
                      ))}
                    </ul>
                  </SortableContext>
                </DndContext>
              ) : (
                <ul className="mt-3 flex flex-col gap-2">
                  {sources.map((s) => (
                    <li key={s.id} className="flex items-center gap-3 rounded-xl border border-slate-200 p-3">
                      <FileText className="size-8 text-brand-700" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-bold">{s.name}</p>
                        <p className="text-sm text-slate-500">
                          {s.kind === 'docx' ? 'Documento do Word' : 'PDF'} · {formatSize(s.size)}
                        </p>
                      </div>
                      <Button variant="danger" size="sm" icon={<Trash2 className="size-4" />} onClick={() => void remove(s)}>
                        Remover
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-slate-100 pt-5">
                <Button size="lg" icon={<Sparkles className="size-5" />} onClick={() => void read()}>
                  Ler e organizar a prova
                </Button>
                <p className="text-sm text-slate-600">
                  {images || sources.some((s) => s.kind === 'pdf')
                    ? 'Fotos e PDFs digitalizados passam por reconhecimento de texto: confira tudo com atenção depois.'
                    : 'Arquivos do Word são lidos diretamente, sem perder formatação.'}
                </p>
              </div>
            </Card>
          )}
        </>
      ) : (
        <Card className="p-5 sm:p-6">
          <PasteBox paragraphs={pasted} onChange={setPasted} />
          <div className="mt-5 flex flex-wrap gap-3">
            <Button size="lg" disabled={!pasted.some((p) => p.length)} loading={reading} icon={<Sparkles className="size-5" />} onClick={() => void organizePasted()}>
              Organizar o texto
            </Button>
            <Button variant="ghost" onClick={() => navigate(`/prova/${project.id}/conferir`)}>
              Pular e digitar tudo do zero
            </Button>
          </div>
        </Card>
      )}
    </div>
  )
}
