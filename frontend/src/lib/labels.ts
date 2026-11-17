import type { BlockKind, DocType, Severity } from '../api/types'

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

export const DISCIPLINAS = [
  'Língua Portuguesa',
  'Matemática',
  'Ciências',
  'História',
  'Geografia',
  'Língua Inglesa',
  'Arte',
  'Educação Física',
  'Ensino Religioso',
  'Projeto de Leitura',
]

export const ANOS = ['1º ano', '2º ano', '3º ano', '4º ano', '5º ano', '6º ano', '7º ano', '8º ano', '9º ano']
export const TURNOS = ['Matutino', 'Vespertino', 'Noturno', 'Integral']
export const UNIDADES = ['I', 'II', 'III', 'IV']

export function examTitle(meta: { doc_type: DocType; disciplina: string; ano: string; turma: string }): string {
  const kind = DOC_TYPES[meta.doc_type]?.label ?? 'Prova'
  const parts = [meta.disciplina ? `${kind} de ${meta.disciplina}` : kind]
  const turma = `${meta.ano} ${meta.turma}`.trim()
  if (turma) parts.push(turma)
  return parts.join(' · ')
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
