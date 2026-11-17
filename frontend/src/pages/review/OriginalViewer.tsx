import { ChevronLeft, ChevronRight, Crop, ImagePlus, Minus, Plus, ScanEye } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { urls } from '../../api/client'
import type { Project, Provenance } from '../../api/types'
import { Button, IconButton, cx } from '../../components/ui'

interface PageRef {
  sourceId: string
  sourceName: string
  page: number
  image: string
  method: string
  note: string | null
}

type Box = [number, number, number, number]

interface Props {
  project: Project
  focus: Provenance | null
  onCrop: (sourceId: string, page: number, bbox: Box) => void
}

/** The original document, always visible during review: highlight, zoom and crop. */
export function OriginalViewer({ project, focus, onCrop }: Props) {
  const pages = useMemo<PageRef[]>(
    () =>
      project.sources.flatMap((s) =>
        s.pages
          .filter((p) => p.image)
          .map((p) => ({ sourceId: s.id, sourceName: s.name, page: p.index, image: p.image!, method: p.method, note: p.note })),
      ),
    [project.sources],
  )
  const [index, setIndex] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [cropping, setCropping] = useState(false)
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null)
  const [pending, setPending] = useState<Box | null>(null)
  const frame = useRef<HTMLDivElement>(null)
  const highlight = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!focus?.source_id || !focus.page) return
    const target = pages.findIndex((p) => p.sourceId === focus.source_id && p.page === focus.page)
    if (target >= 0) setIndex(target)
  }, [focus, pages])

  // Scroll only inside the viewer so the content list on the right stays where the user is.
  useEffect(() => {
    const box = highlight.current
    const container = scroller.current
    if (!box || !container) return
    const top = box.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop
    container.scrollTo({ top: Math.max(0, top - container.clientHeight / 3), behavior: 'smooth' })
  }, [focus, index])

  if (!pages.length) {
    return (
      <div className="grid h-full place-items-center rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-slate-600">
        <div>
          <ScanEye className="mx-auto size-10 text-slate-400" aria-hidden />
          <p className="mt-2 font-bold">Sem imagem do original</p>
          <p className="text-sm">
            {project.sources.length
              ? 'A visualização do original não pôde ser gerada neste computador.'
              : 'Este conteúdo foi colado ou digitado, então não há arquivo original para comparar.'}
          </p>
        </div>
      </div>
    )
  }

  const current = pages[Math.min(index, pages.length - 1)]
  const showBox = focus?.bbox && focus.source_id === current.sourceId && focus.page === current.page ? focus.bbox : null

  const point = (e: React.PointerEvent) => {
    const rect = frame.current!.getBoundingClientRect()
    return { x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)) }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
        <h2 className="mr-auto font-display font-extrabold text-brand-950">Original</h2>
        <IconButton label="Página anterior" disabled={index === 0} onClick={() => setIndex(index - 1)}>
          <ChevronLeft className="size-5" />
        </IconButton>
        <span className="min-w-20 text-center text-sm font-bold text-slate-700" aria-live="polite">
          Página {index + 1} de {pages.length}
        </span>
        <IconButton label="Próxima página" disabled={index >= pages.length - 1} onClick={() => setIndex(index + 1)}>
          <ChevronRight className="size-5" />
        </IconButton>
        <span className="mx-1 h-6 w-px bg-slate-200" aria-hidden />
        <IconButton label="Diminuir zoom" disabled={zoom <= 1} onClick={() => setZoom(Math.max(1, zoom - 0.5))}>
          <Minus className="size-5" />
        </IconButton>
        <IconButton label="Aumentar zoom" disabled={zoom >= 3} onClick={() => setZoom(Math.min(3, zoom + 0.5))}>
          <Plus className="size-5" />
        </IconButton>
        <Button
          size="sm"
          variant={cropping ? 'primary' : 'secondary'}
          icon={<Crop className="size-4" />}
          aria-pressed={cropping}
          onClick={() => {
            setCropping(!cropping)
            setPending(null)
          }}
        >
          {cropping ? 'Cancelar recorte' : 'Recortar do original'}
        </Button>
      </div>
      {cropping && (
        <p className="bg-brand-50 px-4 py-2 text-sm text-brand-900">
          Arraste sobre a imagem para marcar a figura, tabela ou fórmula que deve ser preservada como imagem.
          <button className="ml-2 font-bold underline" onClick={() => setPending([0, 0, 1, 1])}>
            Usar a página inteira
          </button>
        </p>
      )}
      <div ref={scroller} className="relative flex-1 overflow-auto bg-slate-100 p-3">
        <div
          ref={frame}
          className={cx('relative mx-auto bg-white shadow', cropping && 'cursor-crosshair touch-none')}
          style={{ width: `${zoom * 100}%` }}
          onPointerDown={(e) => {
            if (!cropping) return
            const p = point(e)
            ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
            setPending(null)
            setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y })
          }}
          onPointerMove={(e) => {
            if (!drag) return
            const p = point(e)
            setDrag({ ...drag, x1: p.x, y1: p.y })
          }}
          onPointerUp={() => {
            if (!drag) return
            const box: Box = [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)]
            setDrag(null)
            if (box[2] - box[0] > 0.02 && box[3] - box[1] > 0.01) setPending(box)
          }}
        >
          <img
            src={urls.page(project.id, current.image)}
            alt={`Página ${current.page} do original (${current.sourceName})`}
            className="block w-full select-none"
            draggable={false}
          />
          {showBox && !cropping && (
            <div
              ref={highlight}
              className="pointer-events-none absolute rounded-md border-[3px] border-amber-500 bg-amber-300/25 shadow-[0_0_0_9999px_rgba(15,23,42,0.08)]"
              style={{
                left: `${showBox[0] * 100 - 0.5}%`,
                top: `${showBox[1] * 100 - 0.4}%`,
                width: `${(showBox[2] - showBox[0]) * 100 + 1}%`,
                height: `${(showBox[3] - showBox[1]) * 100 + 0.8}%`,
              }}
              aria-hidden
            />
          )}
          {(drag || pending) && (
            <div
              className="pointer-events-none absolute border-[3px] border-dashed border-brand-600 bg-brand-400/15"
              style={(() => {
                const b = pending ?? [Math.min(drag!.x0, drag!.x1), Math.min(drag!.y0, drag!.y1), Math.max(drag!.x0, drag!.x1), Math.max(drag!.y0, drag!.y1)]
                return { left: `${b[0] * 100}%`, top: `${b[1] * 100}%`, width: `${(b[2] - b[0]) * 100}%`, height: `${(b[3] - b[1]) * 100}%` }
              })()}
              aria-hidden
            />
          )}
        </div>
      </div>
      {pending && (
        <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 bg-brand-50 px-4 py-3">
          <p className="flex-1 text-sm text-brand-950">A área marcada será inserida como imagem no trecho selecionado à direita.</p>
          <Button variant="secondary" size="sm" onClick={() => setPending(null)}>
            Desfazer seleção
          </Button>
          <Button
            size="sm"
            icon={<ImagePlus className="size-4" />}
            onClick={() => {
              onCrop(current.sourceId, current.page, pending)
              setPending(null)
              setCropping(false)
            }}
          >
            Inserir recorte
          </Button>
        </div>
      )}
      {current.note && <p className="border-t border-slate-100 px-4 py-2 text-sm text-slate-600">Leitura automática: {current.note}</p>}
    </div>
  )
}
