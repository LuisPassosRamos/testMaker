import type {
  ExamMeta,
  Exam,
  FontOption,
  Health,
  HeaderTemplate,
  JobState,
  PrintRequest,
  Project,
  ProjectSummary,
  RenderStatus,
  Run,
  Suggestion,
} from './types'

/** Error carrying a message written for the user (the API answers in Portuguese). */
export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

const FALLBACK = 'Não foi possível falar com o servidor. Verifique se o Prova Pronta está aberto e tente de novo.'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: init?.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(0, 'network', FALLBACK)
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = data?.detail
    if (res.status === 401 && detail?.code === 'auth_required') {
      // The session expired or the school changed its code: show the login screen again.
      window.dispatchEvent(new CustomEvent('pp:auth-required'))
    }
    if (detail && typeof detail === 'object' && 'message' in detail) {
      throw new ApiError(res.status, detail.code ?? 'error', detail.message)
    }
    throw new ApiError(res.status, 'error', FALLBACK)
  }
  return data as T
}

const json = (body: unknown) => JSON.stringify(body)

export const api = {
  authStatus: () => request<{ required: boolean; authenticated: boolean }>('/api/auth/status'),
  login: (code: string) => request<{ authenticated: boolean }>('/api/auth/login', { method: 'POST', body: json({ code }) }),
  logout: () => request<{ authenticated: boolean }>('/api/auth/logout', { method: 'POST' }),
  health: () => request<Health>('/api/health'),
  fonts: () => request<FontOption[]>('/api/fonts'),

  createProject: (start_mode: Project['start_mode'], meta: ExamMeta) =>
    request<Project>('/api/projects', { method: 'POST', body: json({ start_mode, meta }) }),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  summaries: (ids: string[]) => request<ProjectSummary[]>(`/api/projects?ids=${ids.join(',')}`),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),

  uploadSources: (id: string, files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return request<{ project: Project; rejected: { name: string; message: string }[] }>(
      `/api/projects/${id}/sources`,
      { method: 'POST', body: form },
    )
  },
  orderSources: (id: string, ids: string[]) =>
    request<Project>(`/api/projects/${id}/sources/order`, { method: 'PUT', body: json({ ids }) }),
  deleteSource: (id: string, sid: string) =>
    request<Project>(`/api/projects/${id}/sources/${sid}`, { method: 'DELETE' }),
  extract: (id: string, replace = false) =>
    request<JobState>(`/api/projects/${id}/extract`, { method: 'POST', body: json({ replace }) }),
  job: (id: string) => request<JobState | null>(`/api/projects/${id}/job`),
  paste: (id: string, paragraphs: Run[][], replace = false) =>
    request<Project>(`/api/projects/${id}/paste`, { method: 'POST', body: json({ paragraphs, replace }) }),
  saveExam: (id: string, revision: number, exam: Exam) =>
    request<{ revision: number }>(`/api/projects/${id}/exam`, { method: 'PUT', body: json({ revision, exam }) }),
  crop: (id: string, source_id: string, page: number, bbox: [number, number, number, number]) =>
    request<{ asset_id: string; width_cm: number }>(`/api/projects/${id}/crops`, {
      method: 'POST',
      body: json({ source_id, page, bbox }),
    }),
  uploadAsset: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ asset_id: string; width_cm: number }>(`/api/projects/${id}/assets`, { method: 'POST', body: form })
  },

  render: (id: string) => request<JobState>(`/api/projects/${id}/render`, { method: 'POST' }),
  renderStatus: (id: string) => request<RenderStatus>(`/api/projects/${id}/render`),
  send: (id: string, render_id: string, req: PrintRequest) =>
    request<{ mode: 'outbox' | 'smtp'; to: string; stored_as: string | null; at: string }>(
      `/api/projects/${id}/send`,
      { method: 'POST', body: json({ render_id, request: req }) },
    ),

  templates: () => request<HeaderTemplate[]>('/api/templates'),
  saveTemplate: (t: HeaderTemplate) =>
    t.id
      ? request<HeaderTemplate>(`/api/templates/${t.id}`, { method: 'PUT', body: json(t) })
      : request<HeaderTemplate>('/api/templates', { method: 'POST', body: json(t) }),
  deleteTemplate: (tid: string) => request<void>(`/api/templates/${tid}`, { method: 'DELETE' }),
  restoreDefaultTemplate: () => request<HeaderTemplate>('/api/templates/padrao-madre/restore', { method: 'POST' }),
  uploadLogo: (tid: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<HeaderTemplate>(`/api/templates/${tid}/logo`, { method: 'POST', body: form })
  },

  aiStructure: (id: string) =>
    request<Suggestion[]>(`/api/projects/${id}/ai/structure`, { method: 'POST', body: json({ consent: true }) }),
  aiOcrReview: (id: string, block_ids?: string[]) =>
    request<Suggestion[]>(`/api/projects/${id}/ai/ocr-review`, {
      method: 'POST',
      body: json({ consent: true, block_ids }),
    }),
}

export const urls = {
  page: (id: string, image: string) => `/api/projects/${id}/pages/${image}`,
  asset: (id: string, assetId: string) => `/api/projects/${id}/assets/${assetId}`,
  renderPage: (id: string, rid: string, n: number) => `/api/projects/${id}/renders/${rid}/pages/${n}.jpg`,
  download: (id: string, rid: string, kind: 'pdf' | 'docx', inline = false) =>
    `/api/projects/${id}/renders/${rid}/download/${kind}${inline ? '?inline=true' : ''}`,
  logo: (tid: string, bust = '') => `/api/templates/${tid}/logo${bust ? `?v=${bust}` : ''}`,
}
