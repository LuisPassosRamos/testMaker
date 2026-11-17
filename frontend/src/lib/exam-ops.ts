/**
 * Pure editing operations on the exam model. They mutate a draft (the store clones the
 * exam before calling them) and never discard text: when a question number or an
 * alternative label stops being a label, it goes back into the text it came from.
 */
import type { AnswerSpace, Block, BlockKind, Exam, Issue, Part, PartKind, Run } from '../api/types'
import { dropPrefix, normalizeRuns, runsEqual, runsText } from './runs'

export function newId(prefix: string): string {
  const bytes = crypto.getRandomValues(new Uint8Array(5))
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

const NO_ANSWER: AnswerSpace = { kind: 'none', lines: 5, height_cm: 4 }
const QUESTION_LABEL = /^\s*((?:quest[ãa]o\s*)?\d{1,3}\s*[ºª°]?\s*(?:[.):\-–—]|\s[-–—]))\s*/i
const ALT_LABEL = /^\s*(\(\s*[a-hA-H]\s*\)|[a-hA-H]\s*\)|[a-eA-E][.\-–—](?=\s)|\(\s*\))\s*/

export function makePart(kind: PartKind, init: Partial<Part> = {}): Part {
  return {
    id: newId('p'),
    kind,
    runs: [],
    original_runs: null,
    label: kind === 'alternative' ? '' : null,
    original_label: null,
    align: null,
    asset_id: null,
    width_cm: null,
    width_pct: 100,
    rows: null,
    answer: kind === 'answer_space' ? { kind: 'lines', lines: 5, height_cm: 4 } : null,
    provenance: { source_id: null, page: null, bbox: null, method: 'manual', confidence: null },
    issues: [],
    ...init,
  }
}

export function makeBlock(kind: BlockKind, init: Partial<Block> = {}): Block {
  return {
    id: newId('b'),
    kind,
    number: kind === 'question' ? '' : null,
    original_number: null,
    parts: [],
    answer_space: { ...NO_ANSWER },
    page_break_before: false,
    allow_split: false,
    alternatives_inline: false,
    issues: [],
    reviewed: false,
    ...init,
  }
}

// ----------------------------------------------------------------- lookups

export function findBlock(exam: Exam, blockId: string): Block | undefined {
  return exam.blocks.find((b) => b.id === blockId)
}

export function findPart(exam: Exam, partId: string): { block: Block; part: Part; index: number } | undefined {
  for (const block of exam.blocks) {
    const index = block.parts.findIndex((p) => p.id === partId)
    if (index >= 0) return { block, part: block.parts[index], index }
  }
  return undefined
}

export function nextQuestionNumber(exam: Exam, beforeIndex = exam.blocks.length): string {
  let last = 0
  exam.blocks.slice(0, beforeIndex).forEach((b) => {
    const n = b.kind === 'question' ? parseInt((b.number ?? '').replace(/\D+/g, ''), 10) : NaN
    if (!Number.isNaN(n)) last = n
  })
  return `${last + 1})`
}

// ------------------------------------------------------------------ status

export function isEdited(part: Part): boolean {
  if (part.original_runs === null) return false
  return !runsEqual(part.runs, part.original_runs) || (part.label ?? null) !== (part.original_label ?? null)
}

export function openIssues(block: Block): Issue[] {
  return [...block.issues, ...block.parts.flatMap((p) => p.issues)].filter((i) => !i.resolved)
}

export function hasLayoutChange(block: Block): boolean {
  return block.page_break_before || block.allow_split || block.alternatives_inline
}

export function allOpenIssues(exam: Exam): { issue: Issue; blockId: string | null }[] {
  const out: { issue: Issue; blockId: string | null }[] = exam.document_issues
    .filter((i) => !i.resolved)
    .map((issue) => ({ issue, blockId: null }))
  for (const block of exam.blocks) for (const issue of openIssues(block)) out.push({ issue, blockId: block.id })
  return out
}

export function resolveIssue(exam: Exam, issueId: string, resolved = true): void {
  const all = [
    ...exam.document_issues,
    ...exam.blocks.flatMap((b) => [...b.issues, ...b.parts.flatMap((p) => p.issues)]),
  ]
  const issue = all.find((i) => i.id === issueId)
  if (issue) issue.resolved = resolved
}

// --------------------------------------------------------------- block ops

export function moveBlock(exam: Exam, blockId: string, delta: number): void {
  const i = exam.blocks.findIndex((b) => b.id === blockId)
  const j = i + delta
  if (i < 0 || j < 0 || j >= exam.blocks.length) return
  const [block] = exam.blocks.splice(i, 1)
  exam.blocks.splice(j, 0, block)
}

export function insertBlock(exam: Exam, afterBlockId: string | null, block: Block): void {
  const i = afterBlockId ? exam.blocks.findIndex((b) => b.id === afterBlockId) : -1
  exam.blocks.splice(i + 1, 0, block)
}

export function deleteBlock(exam: Exam, blockId: string): void {
  exam.blocks = exam.blocks.filter((b) => b.id !== blockId)
}

/** Put a question label back at the start of the block's first paragraph. */
function numberToText(block: Block): void {
  const label = (block.number ?? '').trim()
  if (!label) return
  const first = block.parts.find((p) => p.kind === 'paragraph')
  if (first && block.parts.indexOf(first) === 0) {
    first.runs = normalizeRuns([{ text: `${label} ` }, ...first.runs])
  } else {
    block.parts.unshift(makePart('paragraph', { runs: [{ text: label }] }))
  }
  block.number = null
}

/** Take a label written at the start of the first paragraph ("3) …") as the question number. */
function textToNumber(block: Block): void {
  const first = block.parts[0]
  if (first?.kind === 'paragraph') {
    const m = QUESTION_LABEL.exec(runsText(first.runs))
    if (m) {
      block.number = m[1].trim()
      first.runs = dropPrefix(first.runs, m[0].length)
      return
    }
  }
  block.number = block.number ?? ''
}

export function changeBlockKind(exam: Exam, blockId: string, kind: BlockKind): void {
  const block = findBlock(exam, blockId)
  if (!block || block.kind === kind) return
  if (block.kind === 'question' && kind !== 'question') numberToText(block)
  if (kind === 'question' && block.kind !== 'question') textToNumber(block)
  block.kind = kind
}

export function mergeWithPrevious(exam: Exam, blockId: string): void {
  const i = exam.blocks.findIndex((b) => b.id === blockId)
  if (i <= 0) return
  const block = exam.blocks[i]
  const previous = exam.blocks[i - 1]
  if (block.kind === 'question') numberToText(block)
  previous.parts.push(...block.parts)
  previous.issues.push(...block.issues)
  if (previous.answer_space.kind === 'none') previous.answer_space = block.answer_space
  exam.blocks.splice(i, 1)
}

export function splitBlockAt(exam: Exam, partId: string, kind: BlockKind): string | null {
  const found = findPart(exam, partId)
  if (!found || found.index === 0 && found.block.kind === kind) return null
  const { block, index } = found
  const moved = block.parts.splice(index)
  const created = makeBlock(kind, { parts: moved })
  if (block.kind === 'question' && kind === 'question') {
    created.answer_space = block.answer_space
    block.answer_space = { ...NO_ANSWER }
  }
  if (kind === 'question') textToNumber(created)
  const i = exam.blocks.indexOf(block)
  exam.blocks.splice(i + 1, 0, created)
  if (block.parts.length === 0 && block.kind !== 'question') exam.blocks.splice(i, 1)
  return created.id
}

// ---------------------------------------------------------------- part ops

export function movePart(exam: Exam, partId: string, delta: number): void {
  const found = findPart(exam, partId)
  if (!found) return
  const j = found.index + delta
  if (j < 0 || j >= found.block.parts.length) return
  const [part] = found.block.parts.splice(found.index, 1)
  found.block.parts.splice(j, 0, part)
}

export function movePartToBlock(exam: Exam, partId: string, targetBlockId: string): void {
  const found = findPart(exam, partId)
  const target = findBlock(exam, targetBlockId)
  if (!found || !target || found.block.id === targetBlockId) return
  const [part] = found.block.parts.splice(found.index, 1)
  // Images go right after the statement (before the first alternative) of the new question.
  const firstAlt = target.parts.findIndex((p) => p.kind === 'alternative')
  if ((part.kind === 'image' || part.kind === 'crop') && firstAlt >= 0) target.parts.splice(firstAlt, 0, part)
  else target.parts.push(part)
}

export function insertPart(exam: Exam, blockId: string, afterPartId: string | null, part: Part): void {
  const block = findBlock(exam, blockId)
  if (!block) return
  const i = afterPartId ? block.parts.findIndex((p) => p.id === afterPartId) : -1
  block.parts.splice(i + 1, 0, part)
}

export function deletePart(exam: Exam, partId: string): void {
  const found = findPart(exam, partId)
  if (found) found.block.parts.splice(found.index, 1)
}

export function setPartRuns(exam: Exam, partId: string, runs: Run[]): void {
  const found = findPart(exam, partId)
  if (found) found.part.runs = normalizeRuns(runs)
}

export function restoreOriginal(exam: Exam, partId: string): void {
  const found = findPart(exam, partId)
  if (!found || found.part.original_runs === null) return
  found.part.runs = structuredClone(found.part.original_runs)
  found.part.label = found.part.original_label
}

export function setPartKind(exam: Exam, partId: string, kind: 'paragraph' | 'alternative'): void {
  const found = findPart(exam, partId)
  if (!found || found.part.kind === kind) return
  const part = found.part
  if (kind === 'alternative') {
    const m = ALT_LABEL.exec(runsText(part.runs))
    part.label = m ? m[1].trim() : ''
    if (m) part.runs = dropPrefix(part.runs, m[0].length)
  } else if (part.label) {
    part.runs = normalizeRuns([{ text: `${part.label} ` }, ...part.runs])
    part.label = null
  }
  part.kind = kind
}

export function questionBlocks(exam: Exam): Block[] {
  return exam.blocks.filter((b) => b.kind === 'question')
}
