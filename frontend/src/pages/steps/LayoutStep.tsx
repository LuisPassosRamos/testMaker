import { ArrowLeft, ArrowRight, FileText, LayoutTemplate, Printer, Rows3, Type } from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import type { Exam, ExamMeta, HeaderTemplate, LayoutSettings } from '../../api/types'
import { Button, Card, Field, Input, Segmented, Select, Toggle } from '../../components/ui'
import { useSystem } from '../../hooks/useHealth'
import { useRender } from '../../hooks/useRender'
import { findBlock } from '../../lib/exam-ops'
import { ANOS, UNIDADES } from '../../lib/labels'
import { flushSave, useExamStore } from '../../store/examStore'
import { CheckList, PdfPreview } from '../review/PdfPreview'

const PRESETS: Record<'padrao' | 'economico' | 'ampliado', Partial<LayoutSettings>> = {
  padrao: { font_size: 12, line_spacing: 1.15, question_spacing_pt: 10, margins_cm: { top: 1.5, bottom: 1.5, left: 1.8, right: 1.8 } },
  economico: { font_size: 11, line_spacing: 1.0, question_spacing_pt: 6, margins_cm: { top: 1.2, bottom: 1.2, left: 1.4, right: 1.4 } },
  ampliado: { font_size: 16, line_spacing: 1.5, question_spacing_pt: 14, margins_cm: { top: 1.5, bottom: 1.5, left: 1.8, right: 1.8 } },
}
const MARGINS = {
  estreitas: { top: 1.0, bottom: 1.0, left: 1.2, right: 1.2 },
  normais: { top: 1.5, bottom: 1.5, left: 1.8, right: 1.8 },
  largas: { top: 2.5, bottom: 2.5, left: 2.5, right: 2.5 },
}

function Section({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h3 className="mb-4 flex items-center gap-2 text-lg font-extrabold text-brand-950">
        <span className="text-brand-700">{icon}</span>
        {title}
      </h3>
      <div className="flex flex-col gap-5">{children}</div>
    </Card>
  )
}

/** Range input that records one history entry when the user lets go, not on every pixel. */
function CommitRange({ label, value, min, max, step, unit, onCommit }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onCommit: (value: number) => void
}) {
  const [local, setLocal] = useState(value)
  useEffect(() => setLocal(value), [value])
  const commit = () => {
    if (local !== value) onCommit(local)
  }
  return (
    <label className="flex flex-col gap-2">
      <span className="flex justify-between font-bold text-slate-800">
        {label}
        <span className="text-brand-800">
          {local.toLocaleString('pt-BR')} {unit}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
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

export default function LayoutStep() {
  const { project, exam, apply } = useExamStore()
  const navigate = useNavigate()
  const fonts = useSystem((s) => s.fonts)
  const [templates, setTemplates] = useState<HeaderTemplate[]>([])
  const render = useRender(project?.id ?? '')

  useEffect(() => {
    api.templates().then(setTemplates).catch(() => undefined)
  }, [])

  if (!project || !exam) return null
  const layout = exam.layout
  const hasOriginalHeader = exam.blocks.some((b) => b.kind === 'original_header')

  const setLayout = (patch: Partial<LayoutSettings>, summary: string) =>
    apply((d) => {
      d.layout = { ...d.layout, ...patch, preset: 'preset' in patch ? patch.preset! : 'personalizado' }
    }, { kind: 'layout', summary })
  const setMeta = (key: keyof ExamMeta, value: string) =>
    apply((d) => {
      d.meta = { ...d.meta, [key]: value }
    }, { kind: 'content', summary: 'Dados do cabeçalho alterados' })
  const marginKey = (Object.keys(MARGINS) as (keyof typeof MARGINS)[]).find(
    (k) => JSON.stringify(MARGINS[k]) === JSON.stringify(layout.margins_cm),
  )
  const discursiveWithoutSpace = exam.blocks.filter(
    (b) => b.kind === 'question' && b.answer_space.kind === 'none' && !b.parts.some((p) => p.kind === 'alternative' || p.kind === 'answer_space'),
  )

  function addLinesToDiscursive() {
    apply((d: Exam) => {
      for (const b of discursiveWithoutSpace) {
        const target = findBlock(d, b.id)
        if (target) target.answer_space = { kind: 'lines', lines: 5, height_cm: 4 }
      }
    }, { kind: 'layout', summary: `Linhas de resposta adicionadas a ${discursiveWithoutSpace.length} questão(ões)` })
  }

  function openBlock(blockId: string) {
    navigate(`/prova/${project!.id}/conferir`)
    setTimeout(() => document.getElementById(`bloco-${blockId}`)?.scrollIntoView({ block: 'start' }), 300)
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-2xl font-extrabold text-brand-950">Ajuste a impressão</h2>
        <p className="mt-1 max-w-3xl text-slate-600">
          Cada mudança gera de novo a prévia real do PDF ao lado — é exatamente o que vai para a impressora.
        </p>
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="flex flex-col gap-5">
          <Section icon={<LayoutTemplate className="size-5" />} title="Cabeçalho">
            <Field label="Modelo de cabeçalho" hint="Os modelos são cadastrados uma vez e usados em todas as provas.">
              {(id, d) => (
                <Select
                  id={id}
                  aria-describedby={d}
                  value={exam.header.template_id}
                  onChange={(e) => apply((x) => void (x.header.template_id = e.target.value), { kind: 'layout', summary: 'Modelo de cabeçalho trocado' })}
                >
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Link to="/modelos" className="-mt-3 text-sm font-bold text-brand-700 underline-offset-2 hover:underline">
              Editar modelos de cabeçalho
            </Link>
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 font-bold text-slate-800">O que usar no topo da primeira página</legend>
              {[
                { value: 'template', label: 'Cabeçalho da escola (recomendado)' },
                ...(hasOriginalHeader
                  ? [
                      { value: 'original', label: 'O cabeçalho que veio no arquivo' },
                      { value: 'both', label: 'Os dois (cuidado com repetição)' },
                    ]
                  : []),
                { value: 'none', label: 'Sem cabeçalho' },
              ].map((o) => (
                <label key={o.value} className="flex min-h-10 cursor-pointer items-center gap-3 rounded-lg px-2 hover:bg-brand-50">
                  <input
                    type="radio"
                    name="header-mode"
                    className="size-5 accent-brand-700"
                    checked={exam.header.mode === o.value}
                    onChange={() => apply((x) => void (x.header.mode = o.value as Exam['header']['mode']), { kind: 'layout', summary: `Cabeçalho: ${o.label}` })}
                  />
                  {o.label}
                </label>
              ))}
            </fieldset>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Professor(a)" className="col-span-2">
                {(id) => <Input id={id} key={exam.meta.professor} defaultValue={exam.meta.professor} onBlur={(e) => e.target.value !== exam.meta.professor && setMeta('professor', e.target.value)} />}
              </Field>
              <Field label="Disciplina" className="col-span-2">
                {(id) => <Input id={id} key={exam.meta.disciplina} defaultValue={exam.meta.disciplina} onBlur={(e) => e.target.value !== exam.meta.disciplina && setMeta('disciplina', e.target.value)} />}
              </Field>
              <Field label="Ano">
                {(id) => (
                  <Select id={id} value={exam.meta.ano} onChange={(e) => setMeta('ano', e.target.value)}>
                    {ANOS.map((a) => (
                      <option key={a}>{a}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Turma">
                {(id) => <Input id={id} key={exam.meta.turma} defaultValue={exam.meta.turma} onBlur={(e) => e.target.value !== exam.meta.turma && setMeta('turma', e.target.value)} />}
              </Field>
              <Field label="Unidade">
                {(id) => (
                  <Select id={id} value={exam.meta.unidade} onChange={(e) => setMeta('unidade', e.target.value)}>
                    <option value="">—</option>
                    {UNIDADES.map((u) => (
                      <option key={u}>{u}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Data">
                {(id) => <Input id={id} key={exam.meta.data} placeholder="dd/mm/aaaa" defaultValue={exam.meta.data} onBlur={(e) => e.target.value !== exam.meta.data && setMeta('data', e.target.value)} />}
              </Field>
            </div>
          </Section>

          <Section icon={<Type className="size-5" />} title="Texto">
            <div className="flex flex-col gap-2">
              <span className="font-bold text-slate-800">Estilo rápido</span>
              <Segmented
                label="Estilo rápido"
                value={layout.preset}
                options={[
                  { value: 'padrao', label: 'Padrão' },
                  { value: 'economico', label: 'Econômico' },
                  { value: 'ampliado', label: 'Ampliado (baixa visão)' },
                  ...(layout.preset === 'personalizado' ? [{ value: 'personalizado' as const, label: 'Personalizado' }] : []),
                ]}
                onChange={(preset) => {
                  if (preset === 'personalizado') return
                  setLayout({ ...PRESETS[preset], preset }, `Estilo “${preset}” aplicado`)
                }}
              />
            </div>
            <Field label="Fonte">
              {(id) => (
                <Select id={id} value={layout.font_family} onChange={(e) => setLayout({ font_family: e.target.value }, `Fonte alterada para ${e.target.value}`)}>
                  {(fonts.length ? fonts : [{ name: layout.font_family, description: '' }]).map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                      {f.description ? ` — ${f.description}` : ''}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <CommitRange label="Tamanho da letra" value={layout.font_size} min={10} max={20} step={0.5} unit="pt" onCommit={(v) => setLayout({ font_size: v }, `Tamanho da letra: ${v} pt`)} />
            <div className="flex flex-col gap-2">
              <span className="font-bold text-slate-800">Espaço entre linhas</span>
              <Segmented
                label="Espaço entre linhas"
                value={layout.line_spacing}
                options={[1.0, 1.15, 1.5, 2.0].map((v) => ({ value: v, label: v.toLocaleString('pt-BR') }))}
                onChange={(v) => setLayout({ line_spacing: v }, `Entrelinha: ${v}`)}
              />
            </div>
            <CommitRange label="Espaço entre questões" value={layout.question_spacing_pt} min={0} max={36} step={2} unit="pt" onCommit={(v) => setLayout({ question_spacing_pt: v }, `Espaço entre questões: ${v} pt`)} />
            <Toggle label="Número da questão em negrito" checked={layout.bold_question_numbers} onChange={(v) => setLayout({ bold_question_numbers: v }, 'Destaque do número da questão alterado')} />
            <Toggle label="Texto justificado" description="Alinha o texto nas duas margens." checked={layout.justify_text} onChange={(v) => setLayout({ justify_text: v }, 'Alinhamento do texto alterado')} />
          </Section>

          <Section icon={<FileText className="size-5" />} title="Página (A4)">
            <div className="flex flex-col gap-2">
              <span className="font-bold text-slate-800">Colunas</span>
              <Segmented
                label="Colunas"
                value={layout.columns}
                options={[
                  { value: 1, label: '1 coluna' },
                  { value: 2, label: '2 colunas (economiza papel)' },
                ]}
                onChange={(v) => setLayout({ columns: v as 1 | 2 }, `${v} coluna(s)`)}
              />
            </div>
            {layout.columns === 2 && (
              <Toggle label="Linha separando as colunas" checked={layout.column_separator} onChange={(v) => setLayout({ column_separator: v }, 'Linha entre colunas alterada')} />
            )}
            <div className="flex flex-col gap-2">
              <span className="font-bold text-slate-800">Margens</span>
              <Segmented
                label="Margens"
                value={marginKey ?? 'normais'}
                options={[
                  { value: 'estreitas', label: 'Estreitas' },
                  { value: 'normais', label: 'Normais' },
                  { value: 'largas', label: 'Largas' },
                ]}
                onChange={(k) => setLayout({ margins_cm: MARGINS[k as keyof typeof MARGINS] }, `Margens ${k}`)}
              />
            </div>
            <Toggle label="Numerar as páginas" description="“Página 1 de 2” no rodapé." checked={layout.page_numbers} onChange={(v) => setLayout({ page_numbers: v }, 'Numeração de páginas alterada')} />
            <Field label="Texto do rodapé" hint="Opcional. Ex.: “Boa prova!”">
              {(id, d) => (
                <Input id={id} key={layout.footer_text} aria-describedby={d} maxLength={120} defaultValue={layout.footer_text} onBlur={(e) => e.target.value !== layout.footer_text && setLayout({ footer_text: e.target.value }, 'Rodapé alterado')} />
              )}
            </Field>
            <Toggle
              label="Preto e branco"
              description="Converte imagens e logotipo para tons de cinza (mais barato e nítido na copiadora)."
              checked={layout.grayscale}
              onChange={(v) => setLayout({ grayscale: v }, v ? 'Impressão em preto e branco' : 'Impressão colorida')}
            />
          </Section>

          <Section icon={<Rows3 className="size-5" />} title="Respostas">
            <div className="flex flex-col gap-2">
              <span className="font-bold text-slate-800">Altura das linhas de resposta</span>
              <Segmented
                label="Altura das linhas de resposta"
                value={layout.answer_line_spacing_cm}
                options={[
                  { value: 0.6, label: 'Baixa' },
                  { value: 0.8, label: 'Média' },
                  { value: 1.0, label: 'Alta (letra grande)' },
                ]}
                onChange={(v) => setLayout({ answer_line_spacing_cm: v }, 'Altura das linhas de resposta alterada')}
              />
            </div>
            {discursiveWithoutSpace.length > 0 && (
              <div className="rounded-xl bg-brand-50 p-3 text-brand-950">
                <p>{discursiveWithoutSpace.length} questão(ões) sem alternativas estão sem espaço para resposta.</p>
                <Button size="sm" className="mt-2" onClick={addLinesToDiscursive}>
                  Adicionar 5 linhas em cada
                </Button>
              </div>
            )}
          </Section>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {render.status?.render && (
            <CheckList
              checks={render.status.render.checks}
              onOpenBlock={openBlock}
              onNewPage={(blockId) =>
                apply((d) => {
                  const b = findBlock(d, blockId)
                  if (b) b.page_break_before = true
                }, { kind: 'layout', summary: 'Questão passou a começar em nova página', blockId })
              }
            />
          )}
          <div className="rounded-2xl bg-slate-200/60 p-4">
            <PdfPreview projectId={project.id} {...render} onRetry={() => void render.ensure()} />
          </div>
        </div>
      </div>
      <div className="flex flex-wrap justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
        <Button variant="secondary" icon={<ArrowLeft className="size-5" />} onClick={() => navigate(`/prova/${project.id}/conferir`)}>
          Voltar à conferência
        </Button>
        <Button
          size="lg"
          icon={<Printer className="size-5" />}
          onClick={async () => {
            await flushSave()
            navigate(`/prova/${project.id}/finalizar`)
          }}
        >
          Baixar e enviar <ArrowRight className="size-5" aria-hidden />
        </Button>
      </div>
    </div>
  )
}
