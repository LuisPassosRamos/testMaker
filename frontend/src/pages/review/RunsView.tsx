import { Fragment, type ReactNode } from 'react'
import type { Run } from '../../api/types'

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function withHighlights(text: string, words: string[], keyBase: string): ReactNode {
  // Only real words, matched as whole words (never inside another word or on lone punctuation).
  const usable = words.filter((w) => w.trim().length > 1 && /[\p{L}\p{N}]/u.test(w))
  if (!usable.length) return text
  const pattern = new RegExp(String.raw`(?<![\p{L}\p{N}])(` + usable.map(escapeRegExp).join('|') + String.raw`)(?![\p{L}\p{N}])`, 'gu')
  return text.split(pattern).map((piece, i) =>
    usable.includes(piece) ? (
      <mark key={`${keyBase}-${i}`} className="rounded bg-amber-200 px-0.5 text-ink" title="Leitura incerta: compare com o original">
        {piece}
      </mark>
    ) : (
      <Fragment key={`${keyBase}-${i}`}>{piece}</Fragment>
    ),
  )
}

/** Read-only rendering of formatted runs, optionally marking uncertain OCR words. */
export function RunsView({ runs, highlight = [] }: { runs: Run[]; highlight?: string[] }) {
  return (
    <>
      {runs.map((run, i) => {
        let node: ReactNode = run.text.split('\n').map((line, j, all) => (
          <Fragment key={j}>
            {withHighlights(line, highlight, `${i}-${j}`)}
            {j < all.length - 1 && <br />}
          </Fragment>
        ))
        if (run.sup) node = <sup>{node}</sup>
        if (run.sub) node = <sub>{node}</sub>
        if (run.underline) node = <u>{node}</u>
        if (run.italic) node = <em>{node}</em>
        if (run.bold) node = <strong>{node}</strong>
        return <Fragment key={i}>{node}</Fragment>
      })}
    </>
  )
}
