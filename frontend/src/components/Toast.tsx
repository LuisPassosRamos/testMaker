import { CheckCircle2, X, XCircle } from 'lucide-react'
import { create } from 'zustand'

interface ToastItem {
  id: number
  tone: 'success' | 'error' | 'info'
  message: string
}

interface ToastStore {
  items: ToastItem[]
  push: (tone: ToastItem['tone'], message: string) => void
  dismiss: (id: number) => void
}

let counter = 0

export const useToasts = create<ToastStore>((set) => ({
  items: [],
  push: (tone, message) => {
    const id = ++counter
    set((s) => ({ items: [...s.items, { id, tone, message }] }))
    setTimeout(() => set((s) => ({ items: s.items.filter((t) => t.id !== id) })), tone === 'error' ? 9000 : 4500)
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
}))

export const toast = {
  success: (m: string) => useToasts.getState().push('success', m),
  error: (m: string) => useToasts.getState().push('error', m),
  info: (m: string) => useToasts.getState().push('info', m),
}

export function Toaster() {
  const { items, dismiss } = useToasts()
  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4">
      {items.map((t) => (
        <div
          key={t.id}
          role={t.tone === 'error' ? 'alert' : 'status'}
          className={`pointer-events-auto flex max-w-xl items-start gap-3 rounded-xl px-4 py-3 text-white shadow-lift ${
            t.tone === 'error' ? 'bg-rose-700' : t.tone === 'success' ? 'bg-emerald-700' : 'bg-brand-900'
          }`}
        >
          {t.tone === 'error' ? <XCircle className="mt-0.5 size-5 shrink-0" aria-hidden /> : <CheckCircle2 className="mt-0.5 size-5 shrink-0" aria-hidden />}
          <p className="leading-snug">{t.message}</p>
          <button className="ml-2 rounded p-0.5 hover:bg-white/20" onClick={() => dismiss(t.id)} aria-label="Fechar aviso">
            <X className="size-4" />
          </button>
        </div>
      ))}
    </div>
  )
}
