import type { AnswerSpace } from '../../api/types'
import { Segmented, inputBase } from '../../components/ui'

const KINDS: { value: AnswerSpace['kind']; label: string }[] = [
  { value: 'none', label: 'Nenhum' },
  { value: 'lines', label: 'Linhas' },
  { value: 'box', label: 'Quadro' },
  { value: 'blank', label: 'Em branco' },
]

export function AnswerSpaceEditor({
  value,
  onChange,
  allowNone = true,
  label = 'Espaço para a resposta',
}: {
  value: AnswerSpace
  onChange: (next: AnswerSpace) => void
  allowNone?: boolean
  label?: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="font-bold text-slate-700">{label}</span>
      <Segmented label={label} value={value.kind} options={allowNone ? KINDS : KINDS.slice(1)} onChange={(kind) => onChange({ ...value, kind })} />
      {value.kind === 'lines' && (
        <label className="flex items-center gap-2 text-slate-700">
          <input
            type="number"
            min={1}
            max={40}
            value={value.lines}
            onChange={(e) => onChange({ ...value, lines: Math.min(40, Math.max(1, Number(e.target.value) || 1)) })}
            className={`${inputBase} w-20`}
          />
          linhas
        </label>
      )}
      {(value.kind === 'box' || value.kind === 'blank') && (
        <label className="flex items-center gap-2 text-slate-700">
          <input
            type="number"
            min={1}
            max={25}
            step={0.5}
            value={value.height_cm}
            onChange={(e) => onChange({ ...value, height_cm: Math.min(25, Math.max(1, Number(e.target.value) || 1)) })}
            className={`${inputBase} w-24`}
          />
          cm de altura
        </label>
      )}
    </div>
  )
}
