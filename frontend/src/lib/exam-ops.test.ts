import { describe, expect, it } from 'vitest'
import type { Exam } from '../api/types'
import {
  changeBlockKind,
  isEdited,
  makeBlock,
  makePart,
  mergeWithPrevious,
  moveBlock,
  movePartToBlock,
  nextQuestionNumber,
  resolveIssue,
  restoreOriginal,
  setPartKind,
  splitBlockAt,
} from './exam-ops'
import { runsText } from './runs'

function exam(): Exam {
  const q1 = makeBlock('question', {
    number: '1)',
    parts: [
      makePart('paragraph', { runs: [{ text: 'Quanto é 2 + 2?' }], original_runs: [{ text: 'Quanto é 2 + 2?' }] }),
      makePart('alternative', { label: 'a)', runs: [{ text: '3' }] }),
      makePart('alternative', { label: 'b)', runs: [{ text: '4' }] }),
    ],
    issues: [{ id: 'i1', code: 'x', severity: 'warning', message: 'm', resolved: false }],
  })
  const q2 = makeBlock('question', { number: '2)', parts: [makePart('paragraph', { runs: [{ text: 'Explique.' }] })] })
  return {
    meta: { doc_type: 'prova', title: '', modalidade: 'regular', disciplina: '', unidade: '', periodo: 'unidade', ano: '', turma: '', turno: '', professor: '', data: '', valor: '', data_entrega: '', mensagem_final: '' },
    blocks: [q1, q2],
    layout: {} as Exam['layout'],
    header: { mode: 'template', template_id: 'padrao-madre' },
    changes: [],
    document_issues: [],
  }
}

const allText = (e: Exam) =>
  e.blocks
    .map((b) => [b.number ?? '', ...b.parts.map((p) => `${p.label ?? ''}${runsText(p.runs)}`)].join(' '))
    .join(' ')

describe('exam operations keep every piece of text', () => {
  it('turning a question into a support text puts its number back into the text', () => {
    const e = exam()
    changeBlockKind(e, e.blocks[0].id, 'support_text')
    expect(e.blocks[0].number).toBeNull()
    expect(runsText(e.blocks[0].parts[0].runs)).toBe('1) Quanto é 2 + 2?')
  })

  it('turning text that starts with a label into a question takes the label as its number', () => {
    const e = exam()
    const block = makeBlock('support_text', { parts: [makePart('paragraph', { runs: [{ text: '3. Leia o texto.' }] })] })
    e.blocks.push(block)
    changeBlockKind(e, block.id, 'question')
    expect(block.number).toBe('3.')
    expect(runsText(block.parts[0].runs)).toBe('Leia o texto.')
  })

  it('merging with the previous block keeps the merged question number as text', () => {
    const e = exam()
    const before = allText(e).replace(/\s+/g, '')
    mergeWithPrevious(e, e.blocks[1].id)
    expect(e.blocks).toHaveLength(1)
    expect(allText(e).replace(/\s+/g, '')).toBe(before)
  })

  it('splitting a block creates a new question after it, without losing parts', () => {
    const e = exam()
    const partId = e.blocks[0].parts[1].id
    const created = splitBlockAt(e, partId, 'question')
    expect(created).toBeTruthy()
    expect(e.blocks).toHaveLength(3)
    expect(e.blocks[0].parts).toHaveLength(1)
    expect(e.blocks[1].parts.map((p) => p.label)).toEqual(['a)', 'b)'])
  })

  it('switching between alternative and paragraph moves the label in and out of the text', () => {
    const e = exam()
    const alt = e.blocks[0].parts[1]
    setPartKind(e, alt.id, 'paragraph')
    expect(runsText(alt.runs)).toBe('a) 3')
    setPartKind(e, alt.id, 'alternative')
    expect(alt.label).toBe('a)')
    expect(runsText(alt.runs)).toBe('3')
  })

  it('detects manual corrections and restores the extracted text', () => {
    const e = exam()
    const part = e.blocks[0].parts[0]
    expect(isEdited(part)).toBe(false)
    part.runs = [{ text: 'Quanto é 2 + ' }, { text: '2', bold: true }, { text: '?' }]
    expect(isEdited(part)).toBe(true)
    restoreOriginal(e, part.id)
    expect(isEdited(part)).toBe(false)
  })

  it('moves an image to another question, before its alternatives', () => {
    const e = exam()
    const image = makePart('image', { asset_id: 'abc.png' })
    e.blocks[1].parts.push(image)
    movePartToBlock(e, image.id, e.blocks[0].id)
    expect(e.blocks[0].parts.map((p) => p.kind)).toEqual(['paragraph', 'image', 'alternative', 'alternative'])
    expect(e.blocks[1].parts).toHaveLength(1)
  })

  it('moves blocks within bounds and resolves issues by id', () => {
    const e = exam()
    const first = e.blocks[0].id
    moveBlock(e, first, -1)
    expect(e.blocks[0].id).toBe(first)
    moveBlock(e, first, 1)
    expect(e.blocks[1].id).toBe(first)
    resolveIssue(e, 'i1')
    expect(e.blocks[1].issues[0].resolved).toBe(true)
  })

  it('suggests the next question number from the ones before', () => {
    expect(nextQuestionNumber(exam())).toBe('3)')
    expect(nextQuestionNumber({ ...exam(), blocks: [] })).toBe('1.')
  })
})
