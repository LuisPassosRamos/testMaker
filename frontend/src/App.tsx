import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { api } from './api/client'
import { AppHeader } from './components/AppHeader'
import { Toaster } from './components/Toast'
import { Spinner } from './components/ui'
import { useSystem } from './hooks/useHealth'
import HomePage from './pages/HomePage'
import LoginPage from './pages/LoginPage'
import ProjectPage from './pages/ProjectPage'
import TemplatesPage from './pages/TemplatesPage'

type Access = 'checking' | 'open' | 'signed-in' | 'login'

export default function App() {
  const load = useSystem((s) => s.load)
  const [access, setAccess] = useState<Access>('checking')

  const check = useCallback(() => {
    api
      .authStatus()
      .then((s) => setAccess(!s.required ? 'open' : s.authenticated ? 'signed-in' : 'login'))
      .catch(() => setAccess('open')) // the API itself will report problems on each screen
  }, [])

  useEffect(() => {
    check()
    const onExpired = () => setAccess('login')
    window.addEventListener('pp:auth-required', onExpired)
    return () => window.removeEventListener('pp:auth-required', onExpired)
  }, [check])

  useEffect(() => {
    if (access === 'open' || access === 'signed-in') void load()
  }, [access, load])

  if (access === 'checking') {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Spinner label="Abrindo o Prova Pronta…" />
      </div>
    )
  }
  if (access === 'login') {
    return (
      <div className="flex min-h-dvh flex-col">
        <LoginPage onSuccess={() => setAccess('signed-in')} />
        <Toaster />
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader
        onSignOut={
          access === 'signed-in'
            ? async () => {
                await api.logout().catch(() => undefined)
                setAccess('login')
              }
            : undefined
        }
      />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/prova/:id" element={<Navigate to="conferir" replace />} />
        <Route path="/prova/:id/:step" element={<ProjectPage />} />
        <Route path="/modelos" element={<TemplatesPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <footer className="mt-auto border-t border-slate-200 bg-white/60 py-4 text-center text-sm text-slate-500">
        Prova Pronta · feito para a Escola Madre Mª do Rosário de Almeida II · Santo Antônio de Jesus – BA
      </footer>
      <Toaster />
    </div>
  )
}
