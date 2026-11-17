import { FileText, Home, LayoutTemplate } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { cx } from './ui'

export function AppHeader() {
  const link = ({ isActive }: { isActive: boolean }) =>
    cx(
      'inline-flex min-h-11 items-center gap-2 rounded-xl px-3 font-bold transition-colors',
      isActive ? 'bg-white/15 text-white' : 'text-brand-100 hover:bg-white/10 hover:text-white',
    )
  return (
    <header className="bg-brand-950 text-white">
      <a href="#conteudo" className="skip-link">
        Pular para o conteúdo
      </a>
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <NavLink to="/" className="flex items-center gap-3 rounded-xl pr-2" aria-label="Prova Pronta — início">
          <span className="grid size-12 place-items-center rounded-2xl bg-white p-1 shadow-sm">
            <img src="/logo-madre.png" alt="" className="h-10 w-auto" />
          </span>
          <span className="leading-tight">
            <span className="block font-display text-xl font-extrabold tracking-tight">Prova Pronta</span>
            <span className="block text-sm text-brand-200">Escola Madre Mª do Rosário de Almeida II</span>
          </span>
        </NavLink>
        <nav aria-label="Principal" className="flex items-center gap-1">
          <NavLink to="/" end className={link}>
            <Home className="size-5" aria-hidden /> Início
          </NavLink>
          <NavLink to="/modelos" className={link}>
            <LayoutTemplate className="size-5" aria-hidden /> Modelos de cabeçalho
          </NavLink>
          <a href="/api/docs" className="sr-only">
            <FileText aria-hidden /> Documentação da API
          </a>
        </nav>
      </div>
    </header>
  )
}
