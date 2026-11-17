import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { RenderStatus } from '../api/types'
import { flushSave, useExamStore } from '../store/examStore'
import { useJob } from './useJob'

/**
 * Keeps the real PDF preview in sync with the exam: after edits settle, save, then
 * render DOCX -> PDF on the server (skipped when nothing changed since the last render).
 */
export function useRender(projectId: string, debounceMs = 1200) {
  const exam = useExamStore((s) => s.exam)
  const [status, setStatus] = useState<RenderStatus | null>(null)
  const [rendering, setRendering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { job, track } = useJob(projectId)
  const busy = useRef(false)
  const again = useRef(false)

  const refresh = useCallback(async () => {
    const st = await api.renderStatus(projectId)
    setStatus(st)
    return st
  }, [projectId])

  const ensure = useCallback(async () => {
    if (busy.current) {
      again.current = true
      return
    }
    busy.current = true
    setError(null)
    try {
      const saved = await flushSave()
      if (!saved) {
        setError('As últimas alterações ainda não foram salvas. Verifique a conexão.')
        return
      }
      const st = await refresh()
      if (st.up_to_date) return
      setRendering(true)
      let started
      try {
        started = await api.render(projectId)
      } catch (err) {
        if (err instanceof ApiError && err.code === 'job_running') started = undefined
        else throw err
      }
      const final = await track(started)
      if (final.status === 'failed') setError(final.error ?? 'Não foi possível gerar a prévia.')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível gerar a prévia.')
    } finally {
      busy.current = false
      setRendering(false)
      if (again.current) {
        again.current = false
        void ensure()
      }
    }
  }, [projectId, refresh, track])

  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      void ensure()
      return
    }
    const timer = setTimeout(() => void ensure(), debounceMs)
    return () => clearTimeout(timer)
  }, [exam, ensure, debounceMs])

  return { status, rendering, job, error, ensure }
}
