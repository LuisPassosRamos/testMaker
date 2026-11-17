import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, IconButton } from './ui'

interface DialogProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  size?: 'md' | 'lg'
}

/** Accessible modal built on the native <dialog> element (focus trap and Esc for free). */
export function Dialog({ open, title, onClose, children, footer, size = 'md' }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      aria-labelledby="dialog-title"
      className={`m-auto w-[calc(100%-2rem)] rounded-2xl bg-white p-0 text-ink shadow-lift backdrop:bg-brand-950/40 ${size === 'lg' ? 'max-w-3xl' : 'max-w-lg'}`}
    >
      {open && (
        <div className="flex max-h-[85dvh] flex-col">
          <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
            <h2 id="dialog-title" className="text-xl font-extrabold text-brand-950">
              {title}
            </h2>
            <IconButton label="Fechar" onClick={onClose}>
              <X className="size-5" />
            </IconButton>
          </header>
          <div className="overflow-y-auto px-6 py-5">{children}</div>
          {footer && <footer className="flex flex-wrap justify-end gap-3 border-t border-slate-100 px-6 py-4">{footer}</footer>}
        </div>
      )}
    </dialog>
  )
}

/** Promise-based confirmation, e.g. `if (await confirmDialog({...})) …`. */
export function confirmDialog(opts: {
  title: string
  message: ReactNode
  confirm: string
  cancel?: string
  danger?: boolean
}): Promise<boolean> {
  return new Promise((resolve) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const done = (value: boolean) => {
      resolve(value)
      root.unmount()
      host.remove()
    }
    root.render(
      <Dialog
        open
        title={opts.title}
        onClose={() => done(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => done(false)}>
              {opts.cancel ?? 'Cancelar'}
            </Button>
            <Button variant={opts.danger ? 'danger' : 'primary'} onClick={() => done(true)} autoFocus>
              {opts.confirm}
            </Button>
          </>
        }
      >
        <div className="leading-relaxed text-slate-700">{opts.message}</div>
      </Dialog>,
    )
  })
}
