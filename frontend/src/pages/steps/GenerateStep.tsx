import { BookOpenText, FileImage, FileText, Paperclip, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../../api/client'
import type { GenerationRequest } from '../../api/types'
import { ChoiceChips } from '../../components/ChoiceChips'
import { confirmDialog } from '../../components/Dialog'
import { ProgressCard } from '../../components/ProgressCard'
import { toast } from '../../components/Toast'
import { Alert, Button, Card, Toggle, cx, inputClass } from '../../components/ui'
import { useSystem } from '../../hooks/useHealth'
import { useJob } from '../../hooks/useJob'
import { examTitle } from '../../lib/labels'
import { useExamStore } from '../../store/examStore'

/** Starting points from the school's research (main questions per exam). */
export function suggestedRange(ano: string): [number, number, number] {
  const year = Number((ano.match(/[1-9]/) ?? ['0'])[0])
  if (year === 1) return [4, 7, 5]
  if (year === 2) return [6, 10, 8]
  if (year === 3) return [7, 10, 8]
  return [8, 12, 10]
}

const DEFAULT_REQUEST: GenerationRequest = {
  conteudo: '',
  quantidade: null,
  formato: 'misturado',
  nivel: 'normal',
  com_texto_apoio: true,
  observacoes: '',
}

function Section({ number, title, hint, children }: { number: number; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card className="p-5 sm:p-6">
      <h3 className="text-lg font-extrabold text-brand-950">
        {number}. {title}
      </h3>
      {hint && <p className="mt-1 text-slate-600">{hint}</p>}
      <div className="mt-4 flex flex-col gap-5">{children}</div>
    </Card>
  )
}

export default function GenerateStep() {
  const { project, exam, replaceProject } = useExamStore()
  const navigate = useNavigate()
  const health = useSystem((s) => s.health)
  const { job, track } = useJob(project?.id)
  const [req, setReq] = useState<GenerationRequest>(() => project?.generation ?? DEFAULT_REQUEST)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (project?.job?.status === 'running' && project.job.kind === 'generate') {
      setBusy(true)
      void track(project.job).then(finished)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!project || !exam) return null
  const materials = project.sources.filter((s) => s.role === 'material')
  const [min, max, typical] = suggestedRange(exam.meta.ano)
  const aiReady = Boolean(health?.ai.enabled)
  const set = <K extends keyof GenerationRequest>(key: K, value: GenerationRequest[K]) => setReq((r) => ({ ...r, [key]: value }))

  async function finished(final: { status: string; error?: string | null }) {
    if (!project) return
    const fresh = await api.getProject(project.id)
    replaceProject(fresh)
    setBusy(false)
    if (final.status === 'done') {
      toast.success('Prova criada! Confira cada questão e o gabarito.')
      navigate(`/prova/${project.id}/conferir`)
    }
  }

  async function upload(files: File[]) {
    if (!files.length || !project) return
    setUploading(true)
    try {
      const res = await api.uploadSources(project.id, files, 'material')
      replaceProject(res.project)
      res.rejected.forEach((r) => toast.error(`“${r.name}”: ${r.message}`))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível enviar o material.')
    } finally {
      setUploading(false)
      if (input.current) input.current.value = ''
    }
  }

  async function remove(sid: string) {
    if (!project) return
    replaceProject(await api.deleteSource(project.id, sid))
  }

  async function create() {
    if (!project || !exam) return
    setError(null)
    if (!req.conteudo.trim() && materials.length === 0) {
      setError('Escreva o que foi trabalhado ou envie um material para a IA usar como base.')
      document.getElementById('conteudo-trabalhado')?.focus()
      return
    }
    if (exam.blocks.length) {
      const ok = await confirmDialog({
        title: 'Criar uma nova prova?',
        message: 'A prova atual será substituída pela nova versão criada pela IA.',
        confirm: 'Criar nova versão',
        danger: true,
      })
      if (!ok) return
    }
    setBusy(true)
    try {
      const started = await api.aiGenerate(project.id, req)
      await finished(await track(started))
    } catch (err) {
      setBusy(false)
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível pedir a prova à IA.')
    }
  }

  if (busy && job) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <ProgressCard job={job} title="Criando a prova" />
        <p className="text-center text-slate-600">A IA está escrevendo as questões no padrão da escola. Isso leva de 1 a 2 minutos.</p>
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-extrabold text-brand-950">
          <Sparkles className="size-6 text-brand-700" aria-hidden /> Criar a prova com IA
        </h2>
        <p className="mt-1 text-slate-600">
          {examTitle(exam.meta)}. Conte o que foi trabalhado e a IA monta a prova no padrão da escola, com gabarito. Você confere tudo antes de
          imprimir.
        </p>
      </div>

      {!aiReady && (
        <Alert tone="warning" title="A IA ainda não foi configurada neste site">
          Peça à pessoa responsável pelo site para ativar a IA. Enquanto isso, você pode voltar ao início e enviar uma prova pronta ou digitá-la.
        </Alert>
      )}
      {job?.status === 'failed' && <ProgressCard job={job} title="Criação da prova" />}

      <Section number={1} title="O que foi trabalhado?" hint="Escreva os conteúdos como no seu plano de aula. Quanto mais claro, melhor a prova.">
        <textarea
          id="conteudo-trabalhado"
          className={cx(inputClass, 'min-h-40 py-3 leading-relaxed')}
          maxLength={6000}
          value={req.conteudo}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'erro-conteudo' : undefined}
          placeholder={'Ex.: Ciclo da água (evaporação, condensação, precipitação); estados físicos da água; economia de água em casa.'}
          onChange={(e) => {
            set('conteudo', e.target.value)
            setError(null)
          }}
        />
        {error && (
          <p id="erro-conteudo" className="text-sm font-bold text-rose-700">
            {error}
          </p>
        )}
      </Section>

      <Section
        number={2}
        title="Material de apoio (opcional)"
        hint="Plano de aula, páginas do livro, fotos do caderno ou do quadro. A IA usa como base para as questões."
      >
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" icon={<Paperclip className="size-5" />} loading={uploading} onClick={() => input.current?.click()}>
            Anexar material
          </Button>
          <span className="text-sm text-slate-500">Word, PDF ou fotos (JPG/PNG).</span>
          <input
            ref={input}
            type="file"
            multiple
            className="sr-only"
            accept=".docx,.pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/*,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => void upload(Array.from(e.target.files ?? []))}
            aria-label="Anexar material de apoio"
          />
        </div>
        {materials.length > 0 && (
          <ul className="flex flex-col gap-2">
            {materials.map((m) => (
              <li key={m.id} className="flex items-center gap-3 rounded-xl border border-slate-200 p-3">
                {m.kind === 'image' ? <FileImage className="size-6 text-brand-700" aria-hidden /> : <FileText className="size-6 text-brand-700" aria-hidden />}
                <span className="min-w-0 flex-1 truncate font-bold">{m.name}</span>
                <Button variant="danger" size="sm" icon={<Trash2 className="size-4" />} onClick={() => void remove(m.id)}>
                  Remover
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section number={3} title="Como vai ser a prova?">
        <ChoiceChips
          label="Quantidade de questões"
          required
          value={req.quantidade === null ? 'auto' : String(req.quantidade)}
          options={[
            { value: 'auto', label: `Recomendado (${typical})`, ariaLabel: `Recomendado para o ano: entre ${min} e ${max} questões` },
            ...[5, 8, 10, 12, 15].filter((n) => n !== typical).map((n) => ({ value: String(n) })),
          ]}
          onChange={(v) => set('quantidade', v === 'auto' ? null : Number(v))}
          hint={`Pela pesquisa da escola, provas deste ano costumam ter de ${min} a ${max} questões (até 4 páginas).`}
        />
        <ChoiceChips
          label="Tipo de questões"
          required
          value={req.formato}
          options={[
            { value: 'misturado', label: 'Misturadas' },
            { value: 'objetivas', label: 'Só objetivas (marcar)' },
            { value: 'abertas', label: 'Só abertas (escrever)' },
          ]}
          onChange={(v) => set('formato', v as GenerationRequest['formato'])}
        />
        <ChoiceChips
          label="Nível"
          required
          value={req.nivel}
          options={[
            { value: 'facil', label: 'Mais fácil' },
            { value: 'normal', label: 'Normal' },
            { value: 'desafiador', label: 'Mais desafiador' },
          ]}
          onChange={(v) => set('nivel', v as GenerationRequest['nivel'])}
        />
        <Toggle
          label="Pode usar texto de apoio"
          description="Textos curtos, poemas, notícias ou tabelas para as questões de leitura."
          checked={req.com_texto_apoio}
          onChange={(v) => set('com_texto_apoio', v)}
        />
        <label className="flex flex-col gap-1.5">
          <span className="font-bold text-slate-800">Algo mais? (opcional)</span>
          <textarea
            className={cx(inputClass, 'min-h-20 py-2')}
            maxLength={2000}
            value={req.observacoes}
            placeholder="Ex.: incluir uma questão com tabela; a turma tem dois alunos com dificuldade de leitura."
            onChange={(e) => set('observacoes', e.target.value)}
          />
        </label>
      </Section>

      {aiReady && health?.ai.notice && (
        <p className="flex items-start gap-2 text-sm text-slate-600">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-emerald-700" aria-hidden />
          {health.ai.notice}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <Button size="lg" icon={<Sparkles className="size-5" />} disabled={!aiReady} onClick={() => void create()}>
          {exam.blocks.length ? 'Criar uma nova versão' : 'Criar a prova'}
        </Button>
        {exam.blocks.length > 0 && (
          <Button variant="ghost" icon={<BookOpenText className="size-5" />} onClick={() => navigate(`/prova/${project.id}/conferir`)}>
            Voltar para a prova atual
          </Button>
        )}
      </div>
    </div>
  )
}
