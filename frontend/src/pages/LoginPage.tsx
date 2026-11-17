import { Eye, EyeOff, LogIn } from 'lucide-react'
import { useState } from 'react'
import { ApiError, api } from '../api/client'
import { GlobePattern } from '../components/GlobePattern'
import { Alert, Button, Field, inputBase } from '../components/ui'

/** Shown when the school set an access code: typed once per device. */
export default function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [code, setCode] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!code.trim()) {
      setError('Digite o código da escola.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.login(code)
      onSuccess()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Não foi possível entrar. Tente de novo.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main id="conteudo" className="relative flex flex-1 items-center justify-center overflow-hidden bg-gradient-to-br from-brand-900 via-brand-800 to-brand-700 px-4 py-12">
      <GlobePattern className="pointer-events-none absolute -right-24 -bottom-24 size-[30rem] text-white/10" />
      <div className="relative w-full max-w-md rounded-3xl bg-white p-7 shadow-lift sm:p-9">
        <div className="flex flex-col items-center text-center">
          <span className="grid size-20 place-items-center rounded-3xl bg-white shadow-card ring-1 ring-slate-100">
            <img src="/logo-madre.png" alt="" className="h-16 w-auto" />
          </span>
          <h1 className="mt-4 text-3xl font-extrabold text-brand-950">Prova Pronta</h1>
          <p className="mt-1 text-slate-600">Escola Madre Mª do Rosário de Almeida II</p>
        </div>
        <form onSubmit={submit} className="mt-8 flex flex-col gap-5" noValidate>
          <Field label="Código da escola" hint="Peça o código à secretaria. Você só precisa digitar uma vez neste aparelho." error={error ?? undefined}>
            {(id, d) => (
              <div className="relative">
                <input
                  id={id}
                  aria-describedby={d}
                  aria-invalid={!!error}
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  autoFocus
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className={`${inputBase} min-h-14 w-full pr-14 text-lg`}
                />
                <button
                  type="button"
                  onClick={() => setShow(!show)}
                  aria-label={show ? 'Esconder o código' : 'Mostrar o código'}
                  aria-pressed={show}
                  className="absolute top-1/2 right-2 grid size-10 -translate-y-1/2 place-items-center rounded-lg text-slate-500 hover:bg-brand-50 hover:text-brand-800"
                >
                  {show ? <EyeOff className="size-5" /> : <Eye className="size-5" />}
                </button>
              </div>
            )}
          </Field>
          <Button type="submit" size="lg" loading={busy} icon={<LogIn className="size-5" />}>
            Entrar
          </Button>
        </form>
        <Alert tone="info" className="mt-6">
          Maiúsculas, acentos e espaços não fazem diferença no código.
        </Alert>
      </div>
    </main>
  )
}
