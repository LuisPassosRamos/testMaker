import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { JobState } from '../api/types'

/** Poll a project's background job until it finishes. */
export function useJob(projectId: string | undefined) {
  const [job, setJob] = useState<JobState | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const waiters = useRef<((job: JobState) => void)[]>([])

  const poll = useCallback(async () => {
    if (!projectId) return
    try {
      const current = await api.job(projectId)
      setJob(current)
      if (current && current.status === 'running') {
        timer.current = setTimeout(poll, 600)
      } else if (current) {
        waiters.current.splice(0).forEach((resolve) => resolve(current))
      }
    } catch {
      timer.current = setTimeout(poll, 2000)
    }
  }, [projectId])

  useEffect(() => () => clearTimeout(timer.current), [])

  /** Start polling and resolve when the job is done or failed. */
  const track = useCallback(
    (started?: JobState) => {
      if (started) setJob(started)
      clearTimeout(timer.current)
      const promise = new Promise<JobState>((resolve) => waiters.current.push(resolve))
      timer.current = setTimeout(poll, 300)
      return promise
    },
    [poll],
  )

  return { job, setJob, track, poll }
}
