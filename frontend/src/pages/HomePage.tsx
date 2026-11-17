import {
  ArrowRight,
  BookOpenCheck,
  ClipboardCheck,
  ClipboardPaste,
  FilePlus2,
  FileUp,
  History,
  PencilLine,
  ScanSearch,
  Send,
  SlidersHorizontal,
  Trash2,
  Upload,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { DocType, ExamMeta, Project, ProjectSummary } from '../api/types'
import { confirmDialog } from '../components/Dialog'
import { GlobePattern } from '../components/GlobePattern'
import { toast } from '../components/Toast'
import { Alert, Button, Card, Field, Input, Select, cx } from '../components/ui'
import { useSystem } from '../hooks/useHealth'
import { ANOS, DISCIPLINAS, DOC_TYPES, TURNOS, UNIDADES, examTitle, formatDateBR, relativeTime } from '../lib/labels'
import { forgetProject, loadProfile, recentIds, rememberProject, saveProfile } from '../lib/recent'

const TYPE_ICONS: Record<DocType, ReactNode> = {
  prova: <ClipboardCheck className="size-8" aria-hidden />,
  atividade: <PencilLine className="size-8" aria-hidden />,
  revisao: <BookOpenCheck className="size-8" aria-hidden />,
}

const START_OPTIONS: { value: Project['start_mode']; title: string; text: string; icon: ReactNode; badge?: string }[] = [
  {
    value: 'upload',
    title: 'Enviar arquivo',
    text: 'Word (.docx), PDF ou fotos das páginas.',
    icon: <FileUp className="size-7" aria-hidden />,
    badge: 'Mais usado',
  },
  { value: 'paste', title: 'Colar o texto', text: 'Copie do Word ou Google Docs e cole aqui.', icon: <ClipboardPaste className="size-7" aria-hidden /> },
  { value: 'blank', title: 'Criar do zero', text: 'Digite as questões direto no Prova Pronta.', icon: <FilePlus2 className="size-7" aria-hidden /> },
]

const EMPTY_META: ExamMeta = {
  doc_type: 'prova',
  title: '',
  disciplina: '',
  unidade: '',
  ano: '',
  turma: '',
  turno: '',
  professor: '',
  data: '',
  valor: '',
  data_entrega: '',
}

function RadioCard({
  checked,
  onSelect,
  title,
  text,
  icon,
  badge,
  name,
}: {
  checked: boolean
  onSelect: () => void
  title: string
  text: string
  icon: ReactNode
  badge?: string
  name: string
}) {
  return (
    <label
      className={cx(
        'relative flex cursor-pointer flex-col gap-2 rounded-2xl border-2 p-4 transition-colors',
        checked ? 'border-brand-600 bg-brand-50 shadow-sm' : 'border-slate-200 bg-white hover:border-brand-300',
      )}
    >
      <input type="radio" name={name} className="sr-only" checked={checked} onChange={onSelect} />
      <span className={cx(checked ? 'text-brand-700' : 'text-slate-500')}>{icon}</span>
      <span className="font-display text-lg font-extrabold text-brand-950">{title}</span>
      <span className="text-sm leading-snug text-slate-600">{text}</span>
      {badge && <span className="absolute top-3 right-3 rounded-full bg-brand-700 px-2 py-0.5 text-xs font-bold text-white">{badge}</span>}
      {checked && <span className="sr-only">(selecionado)</span>}
    </label>
  )
}

export default function HomePage() {
  const navigate = useNavigate()
  const health = useSystem((s) => s.health)
  const profile = loadProfile()
  const [meta, setMeta] = useState<ExamMeta>({ ...EMPTY_META, professor: profile.professor, disciplina: profile.disciplina })
  const [dateIso, setDateIso] = useState('')
  const [deliveryIso, setDeliveryIso] = useState('')
  const [start, setStart] = useState<Project['start_mode']>('upload')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [recent, setRecent] = useState<ProjectSummary[]>([])

  useEffect(() => {
    const ids = recentIds()
    if (ids.length) api.summaries(ids).then(setRecent).catch(() => undefined)
  }, [])

  const set = (key: keyof ExamMeta) => (value: string) => setMeta((m) => ({ ...m, [key]: value }))

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const missing: Record<string, string> = {}
    if (meta.professor.trim().length < 2) missing.professor = 'Informe o nome do(a) professor(a).'
    if (meta.disciplina.trim().length < 2) missing.disciplina = 'Informe a disciplina.'
    if (!meta.ano) missing.ano = 'Escolha o ano.'
    if (!meta.turma.trim()) missing.turma = 'Informe a turma (ex.: A).'
    setErrors(missing)
    if (Object.keys(missing).length) {
      document.getElementById(`campo-${Object.keys(missing)[0]}`)?.focus()
      return
    }
    setBusy(true)
    try {
      const payload = {
        ...meta,
        data: dateIso ? formatDateBR(dateIso) : '',
        data_entrega: deliveryIso ? formatDateBR(deliveryIso) : '',
      }
      saveProfile({ professor: meta.professor, disciplina: meta.disciplina })
      const project = await api.createProject(start, payload)
      rememberProject(project.id)
      navigate(`/prova/${project.id}/${start === 'blank' ? 'conferir' : 'enviar'}`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível começar. Tente de novo.')
    } finally {
      setBusy(false)
    }
  }

  async function removeRecent(item: ProjectSummary) {
    const ok = await confirmDialog({
      title: 'Apagar este trabalho?',
      message: `“${examTitle(item)}” e os arquivos enviados serão apagados deste computador. Isso não pode ser desfeito.`,
      confirm: 'Apagar',
      danger: true,
    })
    if (!ok) return
    await api.deleteProject(item.id).catch(() => undefined)
    forgetProject(item.id)
    setRecent((r) => r.filter((x) => x.id !== item.id))
  }

  const warnings = [
    health && !health.ocr.available && 'A leitura de fotos e PDFs digitalizados (OCR) não está disponível neste computador.',
    health && !health.converter.available && 'A geração de PDF não está disponível: instale o LibreOffice (veja o README).',
  ].filter(Boolean) as string[]

  return (
    <main id="conteudo" className="flex-1">
      <section className="relative overflow-hidden bg-gradient-to-br from-brand-900 via-brand-800 to-brand-700 text-white">
        <GlobePattern className="pointer-events-none absolute -top-16 -right-16 size-[26rem] text-white/10" />
        <div className="relative mx-auto max-w-7xl px-4 py-10 sm:px-6 sm:py-14">
          <h1 className="max-w-3xl text-3xl leading-tight font-extrabold sm:text-5xl">
            Sua prova organizada e pronta para imprimir
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-brand-100">
            Envie do jeito que estiver — Word, PDF ou fotos. O Prova Pronta organiza questões e alternativas sem mudar o
            conteúdo, você confere e ele envia para a impressão da escola.
          </p>
          <ol className="mt-8 grid max-w-4xl grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { icon: <Upload className="size-6" />, title: 'Enviar prova' },
              { icon: <ScanSearch className="size-6" />, title: 'Conferir conteúdo' },
              { icon: <SlidersHorizontal className="size-6" />, title: 'Ajustar impressão' },
              { icon: <Send className="size-6" />, title: 'Baixar e enviar' },
            ].map((s, i) => (
              <li key={s.title} className="flex items-center gap-3 rounded-2xl bg-white/10 px-4 py-3 backdrop-blur">
                <span className="grid size-10 place-items-center rounded-xl bg-white text-brand-800" aria-hidden>
                  {s.icon}
                </span>
                <span className="font-bold leading-tight">
                  <span className="block text-sm font-normal text-brand-200">Passo {i + 1}</span>
                  {s.title}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <div className="mx-auto grid max-w-7xl gap-6 px-4 py-8 sm:px-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card className="p-5 sm:p-7">
          <form onSubmit={submit} noValidate className="flex flex-col gap-8">
            <div>
              <h2 className="text-2xl font-extrabold text-brand-950">Começar um novo documento</h2>
              <p className="mt-1 text-slate-600">Leva menos de um minuto. Os campos com * são obrigatórios.</p>
            </div>

            <fieldset>
              <legend className="mb-3 text-lg font-extrabold text-brand-900">1. O que você vai preparar?</legend>
              <div className="grid gap-3 sm:grid-cols-3">
                {(Object.keys(DOC_TYPES) as DocType[]).map((type) => (
                  <RadioCard
                    key={type}
                    name="tipo"
                    checked={meta.doc_type === type}
                    onSelect={() => setMeta((m) => ({ ...m, doc_type: type }))}
                    title={DOC_TYPES[type].label}
                    text={DOC_TYPES[type].description}
                    icon={TYPE_ICONS[type]}
                  />
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="mb-3 text-lg font-extrabold text-brand-900">2. Dados da turma</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Professor(a)" id="campo-professor" required error={errors.professor}>
                  {(id, d) => (
                    <Input
                      id={id}
                      aria-describedby={d}
                      aria-invalid={!!errors.professor}
                      autoComplete="name"
                      value={meta.professor}
                      onChange={(e) => set('professor')(e.target.value)}
                      placeholder="Ex.: Maria da Silva"
                    />
                  )}
                </Field>
                <Field label="Disciplina" id="campo-disciplina" required error={errors.disciplina}>
                  {(id, d) => (
                    <>
                      <Input
                        id={id}
                        list="disciplinas"
                        aria-describedby={d}
                        aria-invalid={!!errors.disciplina}
                        value={meta.disciplina}
                        onChange={(e) => set('disciplina')(e.target.value)}
                        placeholder="Ex.: Matemática"
                      />
                      <datalist id="disciplinas">
                        {DISCIPLINAS.map((d) => (
                          <option key={d} value={d} />
                        ))}
                      </datalist>
                    </>
                  )}
                </Field>
                <Field label="Ano" id="campo-ano" required error={errors.ano}>
                  {(id, d) => (
                    <Select
                      id={id}
                      aria-describedby={d}
                      aria-invalid={!!errors.ano}
                      value={meta.ano}
                      onChange={(e) => set('ano')(e.target.value)}
                    >
                      <option value="">Escolha…</option>
                      {ANOS.map((a) => (
                        <option key={a}>{a}</option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Field label="Turma" id="campo-turma" required error={errors.turma} hint="Ex.: A, B ou Única">
                  {(id, d) => (
                    <Input
                      id={id}
                      aria-describedby={d}
                      aria-invalid={!!errors.turma}
                      value={meta.turma}
                      onChange={(e) => set('turma')(e.target.value)}
                      maxLength={20}
                    />
                  )}
                </Field>
                <Field label="Turno">
                  {(id) => (
                    <Select id={id} value={meta.turno} onChange={(e) => set('turno')(e.target.value)}>
                      <option value="">Não informar</option>
                      {TURNOS.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Field label="Unidade">
                  {(id) => (
                    <Select id={id} value={meta.unidade} onChange={(e) => set('unidade')(e.target.value)}>
                      <option value="">Não informar</option>
                      {UNIDADES.map((u) => (
                        <option key={u} value={u}>
                          {u} Unidade
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Field label={meta.doc_type === 'atividade' ? 'Data da atividade' : 'Data de aplicação'} hint="Pode deixar em branco para o aluno preencher.">
                  {(id, d) => <Input id={id} aria-describedby={d} type="date" value={dateIso} onChange={(e) => setDateIso(e.target.value)} />}
                </Field>
                {meta.doc_type === 'prova' && (
                  <Field label="Valor da prova" hint="Ex.: 10,0 (opcional)">
                    {(id, d) => <Input id={id} aria-describedby={d} inputMode="decimal" value={meta.valor} onChange={(e) => set('valor')(e.target.value)} />}
                  </Field>
                )}
                {meta.doc_type === 'atividade' && (
                  <Field label="Data de entrega" hint="Opcional.">
                    {(id, d) => <Input id={id} aria-describedby={d} type="date" value={deliveryIso} onChange={(e) => setDeliveryIso(e.target.value)} />}
                  </Field>
                )}
              </div>
            </fieldset>

            <fieldset>
              <legend className="mb-3 text-lg font-extrabold text-brand-900">3. Como você quer começar?</legend>
              <div className="grid gap-3 sm:grid-cols-3">
                {START_OPTIONS.map((o) => (
                  <RadioCard key={o.value} name="inicio" checked={start === o.value} onSelect={() => setStart(o.value)} {...o} />
                ))}
              </div>
            </fieldset>

            {warnings.map((w) => (
              <Alert key={w} tone="warning">
                {w}
              </Alert>
            ))}

            <div className="flex flex-wrap items-center gap-4">
              <Button type="submit" size="lg" loading={busy} icon={<ArrowRight className="size-5" aria-hidden />}>
                Continuar
              </Button>
              <p className="text-sm text-slate-600">Tudo é salvo automaticamente enquanto você trabalha.</p>
            </div>
          </form>
        </Card>

        <aside className="flex flex-col gap-6">
          <Card className="p-5">
            <h2 className="flex items-center gap-2 text-lg font-extrabold text-brand-950">
              <History className="size-5 text-brand-700" aria-hidden /> Continuar de onde parou
            </h2>
            {recent.length === 0 ? (
              <p className="mt-2 text-slate-600">Os trabalhos que você começar aparecem aqui.</p>
            ) : (
              <ul className="mt-3 flex flex-col gap-2">
                {recent.map((item) => (
                  <li key={item.id} className="flex items-center gap-2 rounded-xl border border-slate-200 p-3">
                    <button
                      className="min-w-0 flex-1 text-left hover:text-brand-800"
                      onClick={() => {
                        rememberProject(item.id)
                        navigate(`/prova/${item.id}/conferir`)
                      }}
                    >
                      <span className="block truncate font-bold">{examTitle(item)}</span>
                      <span className="block text-sm text-slate-500">
                        {item.questions} questões · {relativeTime(item.updated_at)}
                      </span>
                    </button>
                    <button
                      className="rounded-lg p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-700"
                      aria-label={`Apagar ${examTitle(item)}`}
                      onClick={() => removeRecent(item)}
                    >
                      <Trash2 className="size-5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {health && (
              <p className="mt-3 text-sm text-slate-500">
                Por privacidade, os arquivos ficam guardados por {Math.round(health.limits.retention_hours / 24)} dias e depois são
                apagados.
              </p>
            )}
          </Card>
          <Card className="p-5">
            <h2 className="text-lg font-extrabold text-brand-950">Antes de enviar fotos</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-700">
              <li>Fotografe com boa luz, sem sombra sobre a folha.</li>
              <li>Mantenha o celular reto, com a folha inteira na foto.</li>
              <li>Uma foto por página, na ordem da prova.</li>
              <li>Se tiver o arquivo do Word, prefira ele: a leitura é perfeita.</li>
            </ul>
          </Card>
        </aside>
      </div>
    </main>
  )
}
