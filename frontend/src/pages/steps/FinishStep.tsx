import { AlertTriangle, ArrowLeft, CheckCircle2, Download, ExternalLink, FileDown, Mail, Send } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api, urls } from '../../api/client'
import type { PrintRequest } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { toast } from '../../components/Toast'
import { Alert, Button, Card, Field, Input, Select, Toggle, inputClass } from '../../components/ui'
import { useSystem } from '../../hooks/useHealth'
import { useRender } from '../../hooks/useRender'
import { allOpenIssues } from '../../lib/exam-ops'
import { ANOS, DOC_TYPES, TURNOS, UNIDADES } from '../../lib/labels'
import { loadProfile, saveProfile } from '../../lib/recent'
import { useExamStore } from '../../store/examStore'
import { CheckList } from '../review/PdfPreview'

export default function FinishStep() {
  const { project, exam } = useExamStore()
  const navigate = useNavigate()
  const health = useSystem((s) => s.health)
  const render = useRender(project?.id ?? '', 300)
  const [form, setForm] = useState<PrintRequest>(() => ({
    professor: exam?.meta.professor ?? '',
    disciplina: exam?.meta.disciplina ?? '',
    ano: exam?.meta.ano ?? '',
    turma: exam?.meta.turma ?? '',
    turno: exam?.meta.turno ?? '',
    unidade: exam?.meta.unidade ?? '',
    data_aplicacao: exam?.meta.data ?? '',
    copias: 30,
    frente_verso: true,
    observacoes: '',
    email_professor: loadProfile().email,
  }))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [confirming, setConfirming] = useState(false)
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState<{ mode: string; to: string } | null>(null)

  if (!project || !exam) return null
  const result = render.status?.render
  const ready = Boolean(result && render.status?.up_to_date && !render.rendering)
  const pending = allOpenIssues(exam)
  const pendingErrors = pending.filter((p) => p.issue.severity === 'error').length
  const blockingChecks = result?.checks.filter((c) => c.severity === 'error') ?? []
  const outbox = health?.mail.mode === 'outbox'
  const recipient = health?.mail.to ?? 'escolamadre2impressoes@gmail.com'
  const set = <K extends keyof PrintRequest>(key: K, value: PrintRequest[K]) => setForm((f) => ({ ...f, [key]: value }))

  function validate(): boolean {
    const e: Record<string, string> = {}
    if (form.professor.trim().length < 2) e.professor = 'Informe o nome.'
    if (form.disciplina.trim().length < 2) e.disciplina = 'Informe a disciplina.'
    if (!form.ano) e.ano = 'Escolha o ano.'
    if (!form.turma.trim()) e.turma = 'Informe a turma.'
    if (!form.copias || form.copias < 1) e.copias = 'Informe quantas cópias.'
    if (form.email_professor && !/^\S+@\S+\.\S+$/.test(form.email_professor)) e.email_professor = 'E-mail inválido.'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  async function send() {
    if (!result) return
    setSending(true)
    try {
      const res = await api.send(project!.id, result.render_id, form)
      saveProfile({ email: form.email_professor })
      setSent(res)
      setConfirming(false)
      toast.success(res.mode === 'outbox' ? 'E-mail salvo na caixa de saída de teste.' : 'Enviado para a impressão!')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível enviar.')
      if (err instanceof ApiError && err.code === 'render_outdated') void render.ensure()
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h2 className="text-2xl font-extrabold text-brand-950">Baixar e enviar para a impressão</h2>
        <p className="mt-1 text-slate-600">Confira o resumo, baixe os arquivos ou envie direto para a equipe de impressão da escola.</p>
      </div>

      {pendingErrors > 0 && (
        <Alert
          tone="warning"
          title={`Ainda há ${pendingErrors} trecho(s) marcados como “Precisa revisar”`}
          action={
            <Button size="sm" variant="secondary" onClick={() => navigate(`/prova/${project.id}/conferir`)}>
              Revisar agora
            </Button>
          }
        >
          Eles podem ter erros de leitura. Recomendamos conferir antes de imprimir.
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="flex flex-col gap-6">
          <Card className="p-5">
            <h3 className="text-lg font-extrabold text-brand-950">Resumo</h3>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
              <dt className="text-slate-500">Tipo</dt>
              <dd className="font-bold">{DOC_TYPES[exam.meta.doc_type].label}</dd>
              <dt className="text-slate-500">Questões</dt>
              <dd className="font-bold">{exam.blocks.filter((b) => b.kind === 'question').length}</dd>
              <dt className="text-slate-500">Páginas</dt>
              <dd className="font-bold">{result ? result.pages : '…'}</dd>
              <dt className="text-slate-500">Pendências</dt>
              <dd className="font-bold">{pending.length ? `${pending.length} aviso(s)` : 'Nenhuma'}</dd>
            </dl>
            {result && (
              <div className="mt-4">
                <CheckList checks={result.checks} />
              </div>
            )}
          </Card>

          <Card className="p-5">
            <h3 className="text-lg font-extrabold text-brand-950">Baixar arquivos</h3>
            {!ready && <p className="mt-2 text-slate-600">{render.rendering ? render.job?.message || 'Gerando os arquivos…' : 'Preparando…'}</p>}
            {render.error && <Alert tone="error" className="mt-3">{render.error}</Alert>}
            <div className="mt-4 flex flex-col gap-3">
              <a
                href={ready && result ? urls.download(project.id, result.render_id, 'pdf') : undefined}
                aria-disabled={!ready}
                className={`inline-flex min-h-14 items-center justify-center gap-2 rounded-xl px-6 text-lg font-bold ${ready ? 'bg-brand-700 text-white hover:bg-brand-800' : 'pointer-events-none bg-brand-300 text-white'}`}
              >
                <FileDown className="size-6" aria-hidden /> Baixar PDF para imprimir
              </a>
              <div className="flex flex-wrap gap-3">
                <a
                  href={ready && result ? urls.download(project.id, result.render_id, 'docx') : undefined}
                  aria-disabled={!ready}
                  className={`inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border px-4 font-bold ${ready ? 'border-brand-200 bg-white text-brand-800 hover:bg-brand-50' : 'pointer-events-none border-slate-200 text-slate-400'}`}
                >
                  <Download className="size-5" aria-hidden /> Baixar Word (.docx)
                </a>
                <a
                  href={ready && result ? urls.download(project.id, result.render_id, 'pdf', true) : undefined}
                  target="_blank"
                  rel="noreferrer"
                  aria-disabled={!ready}
                  className={`inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border px-4 font-bold ${ready ? 'border-brand-200 bg-white text-brand-800 hover:bg-brand-50' : 'pointer-events-none border-slate-200 text-slate-400'}`}
                >
                  <ExternalLink className="size-5" aria-hidden /> Abrir PDF
                </a>
              </div>
              {result && (
                <p className="text-sm text-slate-500">
                  Arquivos: “{result.pdf_name}” e “{result.docx_name}”. O PDF é o mesmo da prévia.
                </p>
              )}
            </div>
          </Card>
          <Button variant="secondary" icon={<ArrowLeft className="size-5" />} onClick={() => navigate(`/prova/${project.id}/ajustar`)} className="self-start">
            Voltar ao ajuste de impressão
          </Button>
        </div>

        <Card className="p-5 sm:p-6">
          <h3 className="flex items-center gap-2 text-lg font-extrabold text-brand-950">
            <Mail className="size-5 text-brand-700" aria-hidden /> Enviar para a impressão
          </h3>
          <p className="mt-1 text-slate-600">
            O PDF e o Word vão para <strong>{recipient}</strong> com os dados abaixo.
          </p>
          {outbox && (
            <Alert tone="info" className="mt-3" title="Modo de teste">
              Este computador ainda não tem o envio de e-mail configurado: o e-mail será salvo na pasta de saída (data/outbox), não
              enviado. Veja o README para ativar o envio real.
            </Alert>
          )}
          {sent ? (
            <div className="mt-5 flex flex-col items-center gap-3 rounded-2xl bg-emerald-50 p-6 text-center text-emerald-950">
              <CheckCircle2 className="size-12 text-emerald-600" aria-hidden />
              <p className="text-xl font-extrabold">{sent.mode === 'outbox' ? 'E-mail preparado (modo de teste)' : 'Enviado para a impressão!'}</p>
              <p>
                {sent.mode === 'outbox' ? 'Salvo na caixa de saída de teste deste computador.' : `Destinatário: ${sent.to}.`} Se precisar mudar algo, ajuste e
                envie de novo.
              </p>
              <Button variant="secondary" onClick={() => setSent(null)}>
                Enviar outra vez
              </Button>
            </div>
          ) : (
            <form
              className="mt-5 grid gap-4 sm:grid-cols-2"
              noValidate
              onSubmit={(e) => {
                e.preventDefault()
                if (validate()) setConfirming(true)
              }}
            >
              <Field label="Professor(a)" required error={errors.professor} className="sm:col-span-2">
                {(id, d) => <Input id={id} aria-describedby={d} aria-invalid={!!errors.professor} value={form.professor} onChange={(e) => set('professor', e.target.value)} />}
              </Field>
              <Field label="Disciplina" required error={errors.disciplina}>
                {(id, d) => <Input id={id} aria-describedby={d} aria-invalid={!!errors.disciplina} value={form.disciplina} onChange={(e) => set('disciplina', e.target.value)} />}
              </Field>
              <Field label="Ano" required error={errors.ano}>
                {(id, d) => (
                  <Select id={id} aria-describedby={d} aria-invalid={!!errors.ano} value={form.ano} onChange={(e) => set('ano', e.target.value)}>
                    <option value="">Escolha…</option>
                    {ANOS.map((a) => (
                      <option key={a}>{a}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Turma" required error={errors.turma}>
                {(id, d) => <Input id={id} aria-describedby={d} aria-invalid={!!errors.turma} value={form.turma} onChange={(e) => set('turma', e.target.value)} />}
              </Field>
              <Field label="Turno">
                {(id) => (
                  <Select id={id} value={form.turno} onChange={(e) => set('turno', e.target.value)}>
                    <option value="">—</option>
                    {TURNOS.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Unidade">
                {(id) => (
                  <Select id={id} value={form.unidade} onChange={(e) => set('unidade', e.target.value)}>
                    <option value="">—</option>
                    {UNIDADES.map((u) => (
                      <option key={u}>{u}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Data de aplicação" hint="dd/mm/aaaa">
                {(id, d) => <Input id={id} aria-describedby={d} value={form.data_aplicacao} onChange={(e) => set('data_aplicacao', e.target.value)} />}
              </Field>
              <Field label="Quantidade de cópias" required error={errors.copias}>
                {(id, d) => (
                  <Input
                    id={id}
                    aria-describedby={d}
                    aria-invalid={!!errors.copias}
                    type="number"
                    min={1}
                    max={500}
                    value={form.copias}
                    onChange={(e) => set('copias', Number(e.target.value))}
                  />
                )}
              </Field>
              <div className="flex items-end sm:col-span-2">
                <Toggle label="Imprimir frente e verso" checked={form.frente_verso} onChange={(v) => set('frente_verso', v)} />
              </div>
              <Field label="Observações para a impressão" hint="Ex.: grampear, prova adaptada, entregar até sexta." className="sm:col-span-2">
                {(id, d) => (
                  <textarea
                    id={id}
                    aria-describedby={d}
                    rows={3}
                    maxLength={1000}
                    value={form.observacoes}
                    onChange={(e) => set('observacoes', e.target.value)}
                    className={`${inputClass} py-2`}
                  />
                )}
              </Field>
              <Field label="Seu e-mail (opcional)" hint="Para a equipe responder em caso de dúvida." error={errors.email_professor} className="sm:col-span-2">
                {(id, d) => (
                  <Input id={id} aria-describedby={d} type="email" autoComplete="email" value={form.email_professor} onChange={(e) => set('email_professor', e.target.value)} />
                )}
              </Field>
              <div className="sm:col-span-2">
                {blockingChecks.length > 0 && (
                  <Alert tone="error" className="mb-3" title="A verificação automática encontrou problemas no PDF">
                    Confira os avisos no resumo antes de enviar.
                  </Alert>
                )}
                <Button type="submit" size="lg" disabled={!ready} icon={<Send className="size-5" />} className="w-full">
                  Enviar para a impressão
                </Button>
              </div>
            </form>
          )}
          {project.sent.length > 0 && !sent && (
            <p className="mt-4 text-sm text-slate-500">
              Já enviado {project.sent.length} vez(es). Último envio: {new Date(project.sent[project.sent.length - 1].at).toLocaleString('pt-BR')}.
            </p>
          )}
        </Card>
      </div>

      <Dialog
        open={confirming}
        title="Confirmar o envio"
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Voltar
            </Button>
            <Button loading={sending} icon={<Send className="size-5" />} onClick={() => void send()}>
              {outbox ? 'Salvar e-mail de teste' : 'Enviar agora'}
            </Button>
          </>
        }
      >
        <ul className="flex flex-col gap-2 text-slate-700">
          <li>
            <strong>Para:</strong> {recipient}
          </li>
          <li>
            <strong>Anexos:</strong> {result?.pdf_name} e {result?.docx_name}
          </li>
          <li>
            <strong>Cópias:</strong> {form.copias} · {form.frente_verso ? 'frente e verso' : 'só frente'}
          </li>
          <li>
            <strong>Turma:</strong> {form.ano} {form.turma} {form.turno && `· ${form.turno}`}
          </li>
        </ul>
        {pendingErrors > 0 && (
          <p className="mt-4 flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-amber-950">
            <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden /> Ainda há {pendingErrors} trecho(s) marcados para revisão.
          </p>
        )}
      </Dialog>
    </div>
  )
}
