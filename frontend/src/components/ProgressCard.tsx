import type { JobState } from '../api/types'
import { Alert, Card } from './ui'

/** Progress of a background job, announced to screen readers. */
export function ProgressCard({ job, title }: { job: JobState | null; title: string }) {
  if (!job) return null
  if (job.status === 'failed') {
    return <Alert tone="error" title="Não deu certo desta vez">{job.error ?? job.message}</Alert>
  }
  const percent = Math.round((job.status === 'done' ? 1 : job.progress) * 100)
  return (
    <Card className="p-6">
      <h2 className="text-xl font-extrabold text-brand-950">{title}</h2>
      <p className="mt-2 text-slate-700" aria-live="polite">
        {job.message || 'Trabalhando…'}
      </p>
      <div
        className="mt-4 h-4 w-full overflow-hidden rounded-full bg-brand-100"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label={title}
      >
        <div
          className="progress-animated h-full rounded-full bg-brand-600 transition-[width] duration-500"
          style={{ width: `${Math.max(6, percent)}%` }}
        />
      </div>
      <p className="mt-2 text-sm text-slate-500">
        Pode levar alguns segundos por página. Você pode deixar esta tela aberta; nada se perde se recarregar.
      </p>
    </Card>
  )
}
