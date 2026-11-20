import type { BlockKind, DocType, Modalidade, Periodo, Severity } from '../api/types'

export const DOC_TYPES: Record<DocType, { label: string; title: string; description: string }> = {
  prova: { label: 'Prova', title: 'Prova', description: 'Avaliação com nota, aplicada em sala.' },
  atividade: { label: 'Atividade', title: 'Atividade', description: 'Exercícios para fazer em sala ou em casa.' },
  revisao: { label: 'Revisão', title: 'Revisão', description: 'Material para estudar antes da prova.' },
}

export const BLOCK_KINDS: Record<BlockKind, { label: string; hint: string }> = {
  question: { label: 'Questão', hint: 'Uma questão com enunciado e, se houver, alternativas.' },
  support_text: { label: 'Texto de apoio', hint: 'Texto, imagem ou tabela usado por uma ou mais questões.' },
  instructions: { label: 'Instruções', hint: 'Orientações gerais da prova.' },
  original_header: { label: 'Cabeçalho do arquivo', hint: 'Cabeçalho que veio no arquivo original.' },
  free: { label: 'Outro trecho', hint: 'Qualquer outro conteúdo.' },
  page_break: { label: 'Quebra de página', hint: 'O que vier depois começa em uma nova página.' },
}

export const SEVERITY: Record<Severity, { label: string }> = {
  error: { label: 'Precisa revisar' },
  warning: { label: 'Confira' },
  info: { label: 'Aviso' },
}

// Names as the school writes them in exam titles ("Avaliação II Trimestre - Português 2º ano").
export const DISCIPLINAS = [
  'Português',
  'Matemática',
  'Ciências',
  'História',
  'Geografia',
  'Inglês',
  'Arte',
  'Educação Física',
  'Ensino Religioso',
  'Projeto de Leitura',
]

export const ANOS = ['1º ano', '2º ano', '3º ano', '4º ano', '5º ano', '6º ano', '7º ano', '8º ano', '9º ano']
export const TURNOS = ['Matutino', 'Vespertino', 'Noturno', 'Integral']
export const UNIDADES = ['I', 'II', 'III', 'IV']

export const PERIODOS: Record<Periodo, string> = { unidade: 'Unidade', trimestre: 'Trimestre', periodo: 'Período' }

export const MODALIDADES: Record<Modalidade, { label: string; chip: string; word: string }> = {
  regular: { label: 'Avaliação regular', chip: 'Regular', word: '' },
  final: { label: 'Avaliação final', chip: 'Final', word: 'Final' },
  parcial: { label: 'Parcial', chip: 'Parcial', word: 'Parcial' },
  recuperacao: { label: 'Recuperação', chip: 'Recuperação', word: 'de Recuperação' },
  segunda_chamada: { label: 'Segunda chamada', chip: 'Segunda chamada', word: 'de Segunda Chamada' },
  simulado: { label: 'Simulado', chip: 'Simulado', word: '' },
}

export const TURMAS = ['A', 'B', 'C', 'Única']

/** Chips for the school years: short text ("1º") with the full name for screen readers. */
export const ANO_CHIPS = ANOS.map((a) => ({ value: a, label: a.replace(' ano', ''), ariaLabel: a }))

export const MODALIDADE_CHIPS = (Object.keys(MODALIDADES) as Modalidade[]).map((k) => ({ value: k, label: MODALIDADES[k].chip }))

export const MENSAGENS_FINAIS = ['Boa prova!', 'Você consegue!', 'Você é capaz!!', 'Eu acredito em você!']

/** Same rule as the backend (docx_writer.header_title): the title printed on the exam. */
export function examTitle(meta: {
  doc_type: DocType
  disciplina: string
  ano: string
  title?: string
  modalidade?: Modalidade
  unidade?: string
  periodo?: Periodo
}): string {
  if (meta.title?.trim()) return meta.title.trim()
  const words: string[] = []
  if (meta.doc_type === 'prova') {
    words.push(meta.modalidade === 'simulado' ? 'Simulado' : 'Avaliação')
    const word = MODALIDADES[meta.modalidade ?? 'regular']?.word
    if (word) words.push(word)
  } else {
    words.push(DOC_TYPES[meta.doc_type]?.label ?? 'Atividade')
  }
  const unidade = meta.unidade?.trim()
  if (unidade) {
    words.push(/unidade|trimestre|bimestre|per[íi]odo|semestre/i.test(unidade) ? unidade : `${unidade} ${PERIODOS[meta.periodo ?? 'unidade']}`)
  }
  const right = [meta.disciplina.trim(), meta.ano.trim()].filter(Boolean).join(' ')
  const title = right ? `${words.join(' ')} - ${right}` : words.join(' ')
  return /^\s*1\s*[ºo°]/.test(meta.ano) ? title.toUpperCase() : title
}

export function formatDateBR(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso
}

export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.round(diff / 60000)
  if (minutes < 1) return 'agora mesmo'
  if (minutes < 60) return `há ${minutes} min`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `há ${hours} h`
  const days = Math.round(hours / 24)
  return days === 1 ? 'ontem' : `há ${days} dias`
}
