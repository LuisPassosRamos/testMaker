import { describe, expect, it } from 'vitest'
import { docToRuns, dropPrefix, normalizeRuns, runsEqual, runsToDoc } from './runs'

describe('formatted runs', () => {
  it('round-trips bold, italic, underline, superscript, subscript and line breaks through the editor', () => {
    const runs = [
      { text: 'Assinale a ' },
      { text: 'INCORRETA', bold: true, underline: true },
      { text: ': x' },
      { text: '2', sup: true },
      { text: ' + H' },
      { text: '2', sub: true },
      { text: 'O\nfim', italic: true },
    ]
    expect(docToRuns(runsToDoc(runs))).toEqual(normalizeRuns(runs))
  })

  it('merges neighbours with the same style and drops empty runs', () => {
    expect(normalizeRuns([{ text: 'a' }, { text: '' }, { text: 'b' }, { text: 'c', bold: true }])).toEqual([
      { text: 'ab' },
      { text: 'c', bold: true },
    ])
  })

  it('compares runs by content and formatting', () => {
    expect(runsEqual([{ text: 'ab' }], [{ text: 'a' }, { text: 'b' }])).toBe(true)
    expect(runsEqual([{ text: 'ab' }], [{ text: 'ab', bold: true }])).toBe(false)
  })

  it('drops a label prefix across runs', () => {
    expect(dropPrefix([{ text: '1' }, { text: ') Texto', bold: true }], 2)).toEqual([{ text: 'Texto', bold: true }])
  })
})
