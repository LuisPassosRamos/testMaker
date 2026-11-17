import { Copy, ImageUp, RotateCcw, Save, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api, urls } from '../api/client'
import type { HeaderTemplate } from '../api/types'
import { confirmDialog } from '../components/Dialog'
import { toast } from '../components/Toast'
import { Alert, Button, Card, Field, Input, Spinner, Toggle, cx, inputClass } from '../components/ui'

function HeaderSketch({ template, bust }: { template: HeaderTemplate; bust: string }) {
  const fields = template.fields.filter((f) => f.enabled)
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4 font-[Arial] text-[13px] text-black" aria-label="Prévia aproximada do cabeçalho">
      <div className="flex items-center gap-4">
        {template.show_logo && <img src={urls.logo(template.id, bust)} alt="Logotipo" className="h-16 w-auto grayscale-0" />}
        <div className="flex-1 text-center leading-tight">
          <p className="font-bold">{template.school_name}</p>
          {template.school_lines.filter(Boolean).map((l, i) => (
            <p key={i} className="text-[11px]">
              {l}
            </p>
          ))}
        </div>
      </div>
      {template.show_title && <p className="mt-2 text-center font-bold">AVALIAÇÃO DE MATEMÁTICA – II UNIDADE</p>}
      {fields.length > 0 && (
        <div className="mt-2 grid grid-cols-3 border border-slate-500">
          {fields.map((f) => (
            <span key={f.key} className={cx('border border-slate-400 px-2 py-1', f.key === 'aluno' && 'col-span-2')}>
              <strong>{f.label}:</strong>
            </span>
          ))}
        </div>
      )}
      <p className="mt-2 text-[11px] text-slate-500">Prévia aproximada. A prévia real aparece em “Ajustar impressão”.</p>
    </div>
  )
}

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<HeaderTemplate[] | null>(null)
  const [current, setCurrent] = useState<HeaderTemplate | null>(null)
  const [saving, setSaving] = useState(false)
  const [bust, setBust] = useState(String(Date.now()))
  const logoInput = useRef<HTMLInputElement>(null)

  const load = async (selectId?: string) => {
    const list = await api.templates()
    setTemplates(list)
    setCurrent(structuredClone(list.find((t) => t.id === selectId) ?? list[0]))
  }

  useEffect(() => {
    load().catch((err) => toast.error(err instanceof Error ? err.message : 'Erro ao carregar modelos.'))
  }, [])

  if (!templates || !current) {
    return (
      <main id="conteudo" className="grid flex-1 place-items-center p-10">
        <Spinner label="Carregando modelos…" />
      </main>
    )
  }

  const update = (patch: Partial<HeaderTemplate>) => setCurrent({ ...current, ...patch })

  async function save() {
    setSaving(true)
    try {
      const saved = await api.saveTemplate(current!)
      toast.success('Modelo salvo.')
      await load(saved.id)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Não foi possível salvar.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <main id="conteudo" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
      <h1 className="text-3xl font-extrabold text-brand-950">Modelos de cabeçalho</h1>
      <p className="mt-1 max-w-3xl text-slate-600">
        Cadastre o cabeçalho da escola uma vez e reutilize em todas as provas, atividades e revisões. Escolha quais campos aparecem.
      </p>
      <div className="mt-6 grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <nav aria-label="Modelos" className="flex flex-col gap-2">
          {templates.map((t) => (
            <button
              key={t.id}
              onClick={() => setCurrent(structuredClone(t))}
              aria-current={t.id === current.id}
              className={cx(
                'min-h-12 rounded-xl border-2 px-4 text-left font-bold',
                t.id === current.id ? 'border-brand-600 bg-brand-50 text-brand-900' : 'border-slate-200 bg-white hover:border-brand-300',
              )}
            >
              {t.name}
              {t.builtin && <span className="block text-sm font-normal text-slate-500">Modelo da escola</span>}
            </button>
          ))}
          <Button
            variant="secondary"
            icon={<Copy className="size-4" />}
            onClick={() => setCurrent({ ...structuredClone(current), id: '', name: `${current.name} (cópia)`, builtin: false })}
          >
            Duplicar este modelo
          </Button>
        </nav>

        <Card className="flex flex-col gap-6 p-5 sm:p-6">
          {!current.id && <Alert tone="info">Novo modelo — salve para usá-lo nas provas.</Alert>}
          <HeaderSketch template={current} bust={bust} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nome do modelo" className="sm:col-span-2">
              {(id) => <Input id={id} value={current.name} onChange={(e) => update({ name: e.target.value })} />}
            </Field>
            <Field label="Nome da escola" className="sm:col-span-2">
              {(id) => <Input id={id} value={current.school_name} onChange={(e) => update({ school_name: e.target.value })} />}
            </Field>
            <Field label="Linhas abaixo do nome" hint="Uma informação por linha: endereço, e-mail, cidade, portaria…" className="sm:col-span-2">
              {(id, d) => (
                <textarea
                  id={id}
                  aria-describedby={d}
                  rows={4}
                  className={`${inputClass} py-2`}
                  value={current.school_lines.join('\n')}
                  onChange={(e) => update({ school_lines: e.target.value.split('\n') })}
                />
              )}
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <Toggle label="Mostrar logotipo" checked={current.show_logo} onChange={(v) => update({ show_logo: v })} />
            {current.id && (
              <>
                <Button variant="secondary" size="sm" icon={<ImageUp className="size-4" />} onClick={() => logoInput.current?.click()}>
                  Trocar logotipo
                </Button>
                <input
                  ref={logoInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="sr-only"
                  aria-label="Escolher logotipo"
                  onChange={async (e) => {
                    const file = e.target.files?.[0]
                    if (!file) return
                    try {
                      const updated = await api.uploadLogo(current.id, file)
                      setCurrent(updated)
                      setBust(String(Date.now()))
                      toast.success('Logotipo atualizado.')
                    } catch (err) {
                      toast.error(err instanceof Error ? err.message : 'Não foi possível enviar o logotipo.')
                    }
                    e.target.value = ''
                  }}
                />
              </>
            )}
            <Toggle label="Mostrar título (ex.: AVALIAÇÃO DE MATEMÁTICA)" checked={current.show_title} onChange={(v) => update({ show_title: v })} />
          </div>
          <fieldset>
            <legend className="mb-2 font-bold text-slate-800">Campos do cabeçalho</legend>
            <p className="mb-3 text-sm text-slate-600">
              “Nota” aparece só em provas e “Entrega” só em atividades. Você pode mudar o texto de cada campo.
            </p>
            <ul className="grid gap-2 sm:grid-cols-2">
              {current.fields.map((f, i) => (
                <li key={f.key} className="flex items-center gap-3 rounded-xl border border-slate-200 px-3 py-2">
                  <input
                    type="checkbox"
                    className="size-5 accent-brand-700"
                    checked={f.enabled}
                    aria-label={`Mostrar o campo ${f.label}`}
                    onChange={(e) => {
                      const fields = [...current.fields]
                      fields[i] = { ...f, enabled: e.target.checked }
                      update({ fields })
                    }}
                  />
                  <input
                    className={`${inputClass} min-h-9`}
                    aria-label={`Texto do campo ${f.label}`}
                    value={f.label}
                    onChange={(e) => {
                      const fields = [...current.fields]
                      fields[i] = { ...f, label: e.target.value }
                      update({ fields })
                    }}
                  />
                </li>
              ))}
            </ul>
          </fieldset>
          <div className="flex flex-wrap gap-3 border-t border-slate-100 pt-5">
            <Button loading={saving} icon={<Save className="size-5" />} onClick={() => void save()}>
              Salvar modelo
            </Button>
            {current.builtin ? (
              <Button
                variant="secondary"
                icon={<RotateCcw className="size-5" />}
                onClick={async () => {
                  if (await confirmDialog({ title: 'Restaurar o modelo da escola?', message: 'As alterações feitas neste modelo serão desfeitas.', confirm: 'Restaurar' })) {
                    await api.restoreDefaultTemplate()
                    setBust(String(Date.now()))
                    await load('padrao-madre')
                  }
                }}
              >
                Restaurar original
              </Button>
            ) : (
              current.id && (
                <Button
                  variant="danger"
                  icon={<Trash2 className="size-5" />}
                  onClick={async () => {
                    if (await confirmDialog({ title: `Apagar “${current.name}”?`, message: 'Provas que usam este modelo passarão a usar o modelo da escola.', confirm: 'Apagar', danger: true })) {
                      await api.deleteTemplate(current.id)
                      await load()
                    }
                  }}
                >
                  Apagar modelo
                </Button>
              )
            )}
          </div>
        </Card>
      </div>
    </main>
  )
}
