// Mirrors backend/app/domain/models.py (the intermediate exam model).

export interface Run {
  text: string
  bold?: boolean
  italic?: boolean
  underline?: boolean
  sup?: boolean
  sub?: boolean
}

export type Method = 'docx' | 'pdf_text' | 'ocr' | 'paste' | 'manual' | 'crop'

export interface Provenance {
  source_id: string | null
  page: number | null
  bbox: [number, number, number, number] | null
  method: Method
  confidence: number | null
}

export type Severity = 'info' | 'warning' | 'error'

export interface Issue {
  id: string
  code: string
  severity: Severity
  message: string
  resolved: boolean
  detail?: Record<string, unknown> | null
}

export interface AnswerSpace {
  kind: 'none' | 'lines' | 'box' | 'blank'
  lines: number
  height_cm: number
}

export interface CellContent {
  runs: Run[]
  asset_id: string | null
  width_cm: number | null
}

export interface TableCell {
  content: CellContent[]
  colspan: number
  vmerge: 'restart' | 'continue' | null
}

export type PartKind = 'paragraph' | 'alternative' | 'image' | 'table' | 'crop' | 'answer_space'
export type Align = 'left' | 'center' | 'right' | 'justify'

export interface Part {
  id: string
  kind: PartKind
  runs: Run[]
  original_runs: Run[] | null
  label: string | null
  original_label: string | null
  align: Align | null
  asset_id: string | null
  width_cm: number | null
  width_pct: number
  rows: TableCell[][] | null
  answer: AnswerSpace | null
  provenance: Provenance
  issues: Issue[]
}

export type BlockKind = 'original_header' | 'instructions' | 'support_text' | 'question' | 'free' | 'page_break'

export interface Block {
  id: string
  kind: BlockKind
  number: string | null
  original_number: string | null
  parts: Part[]
  answer_space: AnswerSpace
  page_break_before: boolean
  allow_split: boolean
  alternatives_inline: boolean
  issues: Issue[]
  reviewed: boolean
}

export type DocType = 'prova' | 'atividade' | 'revisao'

export interface ExamMeta {
  doc_type: DocType
  title: string
  disciplina: string
  unidade: string
  ano: string
  turma: string
  turno: string
  professor: string
  data: string
  valor: string
  data_entrega: string
}

export interface Margins {
  top: number
  bottom: number
  left: number
  right: number
}

export interface LayoutSettings {
  preset: 'padrao' | 'economico' | 'ampliado' | 'personalizado'
  margins_cm: Margins
  font_family: string
  font_size: number
  line_spacing: number
  question_spacing_pt: number
  columns: 1 | 2
  column_separator: boolean
  answer_line_spacing_cm: number
  page_numbers: boolean
  footer_text: string
  grayscale: boolean
  bold_question_numbers: boolean
  justify_text: boolean
}

export interface HeaderConfig {
  mode: 'template' | 'original' | 'both' | 'none'
  template_id: string
}

export interface ChangeEntry {
  id: string
  at: string
  kind: 'content' | 'layout' | 'structure'
  summary: string
  block_id: string | null
}

export interface Exam {
  meta: ExamMeta
  blocks: Block[]
  layout: LayoutSettings
  header: HeaderConfig
  changes: ChangeEntry[]
  document_issues: Issue[]
}

export interface SourcePage {
  index: number
  image: string | null
  full_image: string | null
  width: number | null
  height: number | null
  method: 'pending' | 'text' | 'ocr' | 'render' | 'failed'
  note: string | null
}

export interface SourceFile {
  id: string
  name: string
  kind: 'docx' | 'pdf' | 'image'
  stored_as: string
  size: number
  pages: SourcePage[]
  extracted: boolean
}

export interface JobState {
  id: string
  kind: 'extract' | 'render'
  status: 'idle' | 'running' | 'done' | 'failed'
  stage: string
  message: string
  progress: number
  error: string | null
}

export interface SendRecord {
  at: string
  mode: 'outbox' | 'smtp'
  to: string
  render_id: string
}

export interface Project {
  id: string
  created_at: string
  updated_at: string
  revision: number
  start_mode: 'upload' | 'paste' | 'blank'
  exam: Exam
  sources: SourceFile[]
  job: JobState | null
  last_render_id: string | null
  sent: SendRecord[]
}

export interface ProjectSummary {
  id: string
  doc_type: DocType
  disciplina: string
  ano: string
  turma: string
  questions: number
  updated_at: string
}

export interface CheckResult {
  code: string
  severity: Severity
  message: string
  page: number | null
  block_id: string | null
}

export interface RenderResult {
  render_id: string
  revision: number
  pages: number
  checks: CheckResult[]
  docx_name: string
  pdf_name: string
  created_at: number
}

export interface RenderStatus {
  current_key: string
  render: RenderResult | null
  up_to_date: boolean
}

export interface Health {
  ok: boolean
  version: string
  converter: { available: boolean; engine: string | null; message: string }
  ocr: { available: boolean; version: string | null; message: string }
  ai: { enabled: boolean; model: string | null }
  mail: { mode: 'outbox' | 'smtp'; to: string }
  limits: { max_upload_mb: number; max_files: number; max_pdf_pages: number; retention_hours: number }
}

export interface FontOption {
  name: string
  description: string
}

export type FieldKey =
  | 'aluno'
  | 'numero'
  | 'professor'
  | 'disciplina'
  | 'ano_turma'
  | 'turno'
  | 'unidade'
  | 'data'
  | 'nota'
  | 'valor'
  | 'data_entrega'

export interface HeaderField {
  key: FieldKey
  label: string
  enabled: boolean
}

export interface HeaderTemplate {
  id: string
  name: string
  school_name: string
  school_lines: string[]
  show_logo: boolean
  logo: string | null
  show_title: boolean
  fields: HeaderField[]
  builtin: boolean
}

export interface PrintRequest {
  professor: string
  disciplina: string
  ano: string
  turma: string
  turno: string
  unidade: string
  data_aplicacao: string
  copias: number
  frente_verso: boolean
  observacoes: string
  email_professor: string
}

export interface Suggestion {
  kind: 'structure' | 'ocr'
  part_id: string
  block_id: string
  message: string
  action: string | null
  original: string | null
  suggestion: string | null
}
