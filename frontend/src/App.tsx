import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppHeader } from './components/AppHeader'
import { Toaster } from './components/Toast'
import { useSystem } from './hooks/useHealth'
import HomePage from './pages/HomePage'
import ProjectPage from './pages/ProjectPage'
import TemplatesPage from './pages/TemplatesPage'

export default function App() {
  const load = useSystem((s) => s.load)
  useEffect(() => {
    void load()
  }, [load])
  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader />
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
