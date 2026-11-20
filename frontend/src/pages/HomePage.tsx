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
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { DocType, ExamMeta, Modalidade, Project, ProjectSummary } from '../api/types'
import { confirmDialog } from '../components/Dialog'
import { GlobePattern } from '../components/GlobePattern'
import { toast } from '../components/Toast'
import { ChoiceChips, PeriodChoice } from '../components/ChoiceChips'
import { Alert, Button, Card, Field, Input, cx } from '../components/ui'
import { useSystem } from '../hooks/useHealth'
import { ANO_CHIPS, DISCIPLINAS, DOC_TYPES, MODALIDADE_CHIPS, TURMAS, TURNOS, examTitle, formatDateBR, relativeTime } from '../lib/labels'
import { forgetProject, loadProfile, recentIds, rememberProject, saveProfile } from '../lib/recent'

const TYPE_ICONS: Record<DocType, ReactNode> = {
  prova: <ClipboardCheck className="size-8" aria-hidden />,
  atividade: <PencilLine className="size-8" aria-hidden />,
  revisao: <BookOpenCheck className="size-8" aria-hidden />,
}

const START_OPTIONS: { value: Project['start_mode']; title: string; text: string; icon: ReactNode; badge?: string }[] = [
  {
    value: 'generate',
    title: 'Criar com IA',
    text: 'Conte o que foi trabalhado (ou envie o plano de aula) e a IA monta a prova com gabarito.',
    icon: <Sparkles className="size-7" aria-hidden />,
    badge: 'Novo',
  },
  {
    value: 'upload',
    title: 'Formatar uma prova pronta',
    text: 'Envie o Word, PDF ou fotos. A prova é organizada no padrão da escola.',
    icon: <FileUp className="size-7" aria-hidden />,
  },
  { value: 'paste', title: 'Colar o texto', text: 'Copie do Word, do Google Docs ou do ChatGPT e cole aqui.', icon: <ClipboardPaste className="size-7" aria-hidden /> },
  { value: 'blank', title: 'Digitar do zero', text: 'Escreva as questões direto no Prova Pronta.', icon: <FilePlus2 className="size-7" aria-hidden /> },
]

const EMPTY_META: ExamMeta = {
  doc_type: 'prova',
  title: '',
  modalidade: 'regular',
  disciplina: '',
  unidade: '',
  periodo: 'unidade',
  ano: '',
  turma: '',
  turno: '',
  professor: '',
  data: '',
  valor: '',
  data_entrega: '',
  mensagem_final: '',
}

function summaryTitle(item: ProjectSummary): string {
  const title = item.title || examTitle(item)
  return item.turma.trim() ? `${title} · Turma ${item.turma.trim()}` : title
}

function RadioCard({
  checked,
  onSelect,
  title,
  text,
  icon,
  badge,
  name,
  disabled,
  disabledText,
}: {
  checked: boolean
  onSelect: () => void
  title: string
  text: string
  icon: ReactNode
  badge?: string
  name: string
  disabled?: boolean
  disabledText?: string
}) {
  return (
    <label
      className={cx(
        'relative flex flex-col gap-2 rounded-2xl border-2 p-4 transition-colors has-[:focus-visible]:ring-4 has-[:focus-visible]:ring-brand-200',
        disabled ? 'cursor-not-allowed border-slate-200 bg-slate-50 opacity-70' : 'cursor-pointer',
        !disabled && (checked ? 'border-brand-600 bg-brand-50 shadow-sm' : 'border-slate-200 bg-white hover:border-brand-300'),
      )}
    >
      <input type="radio" name={name} className="sr-only" checked={checked} disabled={disabled} onChange={onSelect} />
      <span className={cx(checked ? 'text-brand-700' : 'text-slate-500')}>{icon}</span>
      <span className="font-display text-lg font-extrabold text-brand-950">{title}</span>
      <span className="text-sm leading-snug text-slate-600">{disabled && disabledText ? disabledText : text}</span>
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
  const [chosenStart, setStart] = useState<Project['start_mode'] | null>(null)
  const aiReady = Boolean(health?.ai.enabled)
  // Creating with AI is the suggested start when the site has AI; otherwise formatting a ready exam.
  const start: Project['start_mode'] = chosenStart ?? (aiReady ? 'generate' : 'upload')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [recent, setRecent] = useState<ProjectSummary[]>([])

  useEffect(() => {
    const ids = recentIds()
    if (ids.length) api.summaries(ids).then(setRecent).catch(() => undefined)
  }, [])

  const set = (key: keyof ExamMeta) => (value: string) => {
    setMeta((m) => ({ ...m, [key]: value }))
    setErrors((e) => {
      if (!e[key]) return e
      const rest = { ...e }
      delete rest[key]
      return rest
    })
  }

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
        modalidade: meta.doc_type === 'prova' ? meta.modalidade : 'regular',
        data: dateIso ? formatDateBR(dateIso) : '',
        data_entrega: deliveryIso ? formatDateBR(deliveryIso) : '',
        // The school ends its exams with an encouraging line. A file that is sent in keeps its own.
        mensagem_final: start !== 'upload' && meta.doc_type === 'prova' ? 'Boa prova!' : '',
      }
      saveProfile({ professor: meta.professor, disciplina: meta.disciplina })
      const project = await api.createProject(start, payload)
      rememberProject(project.id)
      const first = { generate: 'criar', blank: 'conferir', upload: 'enviar', paste: 'enviar' }[start]
      navigate(`/prova/${project.id}/${first}`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível começar. Tente de novo.')
    } finally {
      setBusy(false)
    }
  }

  async function removeRecent(item: ProjectSummary) {
    const ok = await confirmDialog({
      title: 'Apagar este trabalho?',
      message: `“${summaryTitle(item)}” e os arquivos enviados serão apagados deste computador. Isso não pode ser desfeito.`,
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
            Sua prova pronta, no padrão da escola
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-brand-100">
            Crie a prova com inteligência artificial a partir do que você trabalhou em sala, ou envie a prova do jeito que estiver —
            Word, PDF ou fotos. O Prova Pronta organiza tudo, você confere e ele envia para a impressão.
          </p>
          <ol className="mt-8 grid max-w-4xl grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { icon: <Sparkles className="size-6" />, title: 'Criar ou enviar' },
              { icon: <ScanSearch className="size-6" />, title: 'Conferir' },
              { icon: <SlidersHorizontal className="size-6" />, title: 'Ajustar (opcional)' },
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
              <legend className="mb-3 text-lg font-extrabold text-brand-900">1. Como você quer começar?</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {START_OPTIONS.map((o) => (
                  <RadioCard
                    key={o.value}
                    name="inicio"
                    checked={start === o.value}
                    onSelect={() => setStart(o.value)}
                    disabled={o.value === 'generate' && !aiReady}
                    disabledText="Disponível assim que a inteligência artificial for ativada neste site."
                    {...o}
                  />
                ))}
              </div>
              {aiReady && health?.ai.notice && start !== 'blank' && (
                <p className="mt-3 flex items-start gap-2 text-sm text-slate-600">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0 text-emerald-700" aria-hidden />
                  {health.ai.notice}
                </p>
              )}
            </fieldset>

            <fieldset>
              <legend className="mb-3 text-lg font-extrabold text-brand-900">2. O que você vai preparar?</legend>
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
              <legend className="mb-3 text-lg font-extrabold text-brand-900">3. Dados da turma</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Professor(a)" id="campo-professor" required error={errors.professor} className="sm:col-span-2">
                  {(id, d) => (
                    <Input
                      id={id}
                      className="max-w-md"
                      aria-describedby={d}
                      aria-invalid={!!errors.professor}
                      autoComplete="name"
                      value={meta.professor}
                      onChange={(e) => set('professor')(e.target.value)}
                      placeholder="Ex.: Maria da Silva"
                    />
                  )}
                </Field>
                <ChoiceChips
                  id="campo-disciplina"
                  className="sm:col-span-2"
                  label="Disciplina"
                  required
                  error={errors.disciplina}
                  value={meta.disciplina}
                  options={DISCIPLINAS}
                  onChange={set('disciplina')}
                  other="Outra"
                  otherPlaceholder="Escreva a disciplina"
                />
                <ChoiceChips
                  id="campo-ano"
                  className="sm:col-span-2"
                  label="Ano"
                  required
                  error={errors.ano}
                  value={meta.ano}
                  options={ANO_CHIPS}
                  onChange={set('ano')}
                />
                <ChoiceChips
                  id="campo-turma"
                  className="sm:col-span-2"
                  label="Turma"
                  required
                  error={errors.turma}
                  value={meta.turma}
                  options={TURMAS}
                  onChange={set('turma')}
                  other="Outra"
                  otherPlaceholder="Ex.: D ou 5º A/B"
                  otherMaxLength={20}
                />
                <ChoiceChips className="sm:col-span-2" label="Turno" value={meta.turno} options={TURNOS} onChange={set('turno')} hint="Opcional. Toque de novo para desmarcar." />
                {meta.doc_type === 'prova' && (
                  <ChoiceChips
                    className="sm:col-span-2"
                    label="Tipo de avaliação"
                    required
                    value={meta.modalidade}
                    options={MODALIDADE_CHIPS}
                    onChange={(v) => setMeta((m) => ({ ...m, modalidade: v as Modalidade }))}
                  />
                )}
                <PeriodChoice
                  className="sm:col-span-2"
                  unidade={meta.unidade}
                  periodo={meta.periodo}
                  onChange={(p) => setMeta((m) => ({ ...m, ...p }))}
                />
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
                      <span className="block truncate font-bold">{summaryTitle(item)}</span>
                      <span className="block text-sm text-slate-500">
                        {item.questions} questões · {relativeTime(item.updated_at)}
                      </span>
                    </button>
                    <button
                      className="rounded-lg p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-700"
                      aria-label={`Apagar ${summaryTitle(item)}`}
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
