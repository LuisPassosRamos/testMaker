import type { JSONContent } from '@tiptap/react'
import type { Run } from '../api/types'

const STYLE_KEYS = ['bold', 'italic', 'underline', 'sup', 'sub'] as const

export function runsText(runs: Run[] | null | undefined): string {
  return (runs ?? []).map((r) => r.text).join('')
}

function sameStyle(a: Run, b: Run): boolean {
  return STYLE_KEYS.every((k) => Boolean(a[k]) === Boolean(b[k]))
}

/** Merge neighbours with identical formatting and drop empty runs. */
export function normalizeRuns(runs: Run[]): Run[] {
  const out: Run[] = []
  for (const run of runs) {
    if (!run.text) continue
    const clean: Run = { text: run.text }
    for (const k of STYLE_KEYS) if (run[k]) clean[k] = true
    const last = out[out.length - 1]
    if (last && sameStyle(last, clean)) last.text += clean.text
    else out.push(clean)
  }
  return out
}

export function runsEqual(a: Run[] | null | undefined, b: Run[] | null | undefined): boolean {
  return JSON.stringify(normalizeRuns(a ?? [])) === JSON.stringify(normalizeRuns(b ?? []))
}

/** Remove the first `count` characters (used when a label is moved out of the text). */
export function dropPrefix(runs: Run[], count: number): Run[] {
  const out: Run[] = []
  let left = count
  for (const run of runs) {
    if (left >= run.text.length) {
      left -= run.text.length
      continue
    }
    out.push({ ...run, text: run.text.slice(left) })
    left = 0
  }
  if (out.length) out[0] = { ...out[0], text: out[0].text.replace(/^\s+/, '') }
  return normalizeRuns(out)
}

// ------------------------------------------------------------------ TipTap

export function runsToDoc(runs: Run[]): JSONContent {
  const content: JSONContent[] = []
  for (const run of runs) {
    const marks = [
      run.bold && { type: 'bold' },
      run.italic && { type: 'italic' },
      run.underline && { type: 'underline' },
      run.sup && { type: 'superscript' },
      run.sub && { type: 'subscript' },
    ].filter(Boolean) as JSONContent['marks']
    run.text.split('\n').forEach((piece, i) => {
      if (i > 0) content.push({ type: 'hardBreak' })
      if (piece) content.push({ type: 'text', text: piece, ...(marks && marks.length ? { marks } : {}) })
    })
  }
  return { type: 'doc', content: [{ type: 'paragraph', content }] }
}

export function docToRuns(doc: JSONContent): Run[] {
  const runs: Run[] = []
  // A line break belongs to the run before it, so "O<br>fim" in italics stays one italic run.
  const lineBreak = () => {
    const last = runs[runs.length - 1]
    if (last) runs[runs.length - 1] = { ...last, text: `${last.text}\n` }
    else runs.push({ text: '\n' })
  }
  ;(doc.content ?? []).forEach((block, index) => {
    if (index > 0) lineBreak()
    for (const node of block.content ?? []) {
      if (node.type === 'hardBreak') {
        lineBreak()
        continue
      }
      if (node.type !== 'text' || !node.text) continue
      const marks = new Set((node.marks ?? []).map((m) => m.type))
      runs.push({
        text: node.text,
        bold: marks.has('bold'),
        italic: marks.has('italic'),
        underline: marks.has('underline'),
        sup: marks.has('superscript'),
        sub: marks.has('subscript'),
      })
    }
  })
  return normalizeRuns(runs)
}

// ------------------------------------------------------------------ paste

const BLOCK_TAGS = new Set(['P', 'DIV', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'TR', 'BLOCKQUOTE', 'PRE', 'TABLE'])

/**
 * Convert clipboard HTML (Word, Google Docs, web pages) into paragraphs of formatted runs,
 * keeping bold, italic, underline, superscript and subscript — nothing else is interpreted.
 */
export function htmlToParagraphs(html: string): Run[][] {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const paragraphs: Run[][] = [[]]
  const current = () => paragraphs[paragraphs.length - 1]
  const breakParagraph = () => {
    if (current().length) paragraphs.push([])
  }

  const walk = (node: Node, style: Omit<Run, 'text'>) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = (node.textContent ?? '').replace(/\s+/g, ' ')
      if (text.trim() || (text && current().length)) current().push({ text, ...style })
      return
    }
    if (!(node instanceof HTMLElement)) return
    if (['STYLE', 'SCRIPT', 'HEAD', 'TITLE', 'META'].includes(node.tagName)) return
    if (node.tagName === 'BR') {
      breakParagraph()
      return
    }
    const css = node.style
    const next = { ...style }
    const tag = node.tagName
    if (tag === 'B' || tag === 'STRONG' || css.fontWeight === 'bold' || Number(css.fontWeight) >= 600) next.bold = true
    if (css.fontWeight === 'normal' || css.fontWeight === '400') next.bold = false
    if (tag === 'I' || tag === 'EM' || css.fontStyle === 'italic') next.italic = true
    if (tag === 'U' || css.textDecoration.includes('underline') || css.textDecorationLine.includes('underline'))
      next.underline = true
    if (tag === 'SUP' || css.verticalAlign === 'super') next.sup = true
    if (tag === 'SUB' || css.verticalAlign === 'sub') next.sub = true
    const isBlock = BLOCK_TAGS.has(tag)
    if (isBlock) breakParagraph()
    if (tag === 'TD' || tag === 'TH') {
      if (current().length) current().push({ text: '\t' })
    }
    node.childNodes.forEach((child) => walk(child, next))
    if (isBlock) breakParagraph()
  }

  walk(doc.body, {})
  return paragraphs
    .map((p) => normalizeRuns(p.map((r) => ({ ...r }))))
    .map((p) => {
      if (p.length) {
        p[0] = { ...p[0], text: p[0].text.replace(/^\s+/, '') }
        const last = p.length - 1
        p[last] = { ...p[last], text: p[last].text.replace(/\s+$/, '') }
      }
      return normalizeRuns(p)
    })
    .filter((p, i, all) => p.length || (i > 0 && all[i - 1].length))
}

export function textToParagraphs(text: string): Run[][] {
  return text.replace(/\r\n/g, '\n').split('\n').map((line) => (line ? [{ text: line }] : []))
}
