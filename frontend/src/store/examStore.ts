/**
 * Editing state for the open exam: undo/redo history, change log and autosave.
 *
 * Every edit goes through `apply`, which clones the exam, runs a pure operation and
 * records what kind of change it was (content, structure or layout) so the user can
 * always tell their corrections apart from layout tweaks and extraction doubts.
 * Unsaved edits are mirrored to localStorage so a crash or network error loses nothing.
 */
import { create } from 'zustand'
import { ApiError, api } from '../api/client'
import type { ChangeEntry, Exam, Project } from '../api/types'
import { newId } from '../lib/exam-ops'

type SaveState = 'saved' | 'dirty' | 'saving' | 'error' | 'conflict'

interface ChangeInfo {
  kind: ChangeEntry['kind']
  summary: string
  blockId?: string | null
}

interface ExamStore {
  project: Project | null
  exam: Exam | null
  revision: number
  past: Exam[]
  future: Exam[]
  saveState: SaveState
  saveError: string | null
  selectedPartId: string | null
  load: (project: Project) => void
  replaceProject: (project: Project) => void
  apply: (mutate: (draft: Exam) => void, change?: ChangeInfo) => void
  undo: () => void
  redo: () => void
  select: (partId: string | null) => void
  save: () => Promise<void>
}

const HISTORY_LIMIT = 80
const draftKey = (id: string) => `prova-pronta:draft:${id}`
let saveTimer: ReturnType<typeof setTimeout> | undefined

function backup(project: Project | null, exam: Exam, revision: number) {
  if (!project) return
  try {
    localStorage.setItem(draftKey(project.id), JSON.stringify({ revision, exam, at: Date.now() }))
  } catch {
    /* storage may be full or disabled: autosave on the server still works */
  }
}

export function readDraft(id: string): { revision: number; exam: Exam; at: number } | null {
  try {
    const raw = localStorage.getItem(draftKey(id))
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function clearDraft(id: string) {
  try {
    localStorage.removeItem(draftKey(id))
  } catch {
    /* ignore */
  }
}

export const useExamStore = create<ExamStore>((set, get) => ({
  project: null,
  exam: null,
  revision: 0,
  past: [],
  future: [],
  saveState: 'saved',
  saveError: null,
  selectedPartId: null,

  load: (project) =>
    set({
      project,
      exam: project.exam,
      revision: project.revision,
      past: [],
      future: [],
      saveState: 'saved',
      saveError: null,
      selectedPartId: null,
    }),

  // Server-side changes (extraction, paste) replace the content but keep undo for later edits.
  replaceProject: (project) =>
    set((s) => ({
      project,
      exam: project.exam,
      revision: project.revision,
      past: s.exam ? [...s.past, s.exam].slice(-HISTORY_LIMIT) : s.past,
      future: [],
      saveState: 'saved',
    })),

  apply: (mutate, change) => {
    const { exam, project, revision } = get()
    if (!exam) return
    const draft = structuredClone(exam)
    mutate(draft)
    if (change) {
      draft.changes = [
        ...draft.changes,
        {
          id: newId('c'),
          at: new Date().toISOString(),
          kind: change.kind,
          summary: change.summary,
          block_id: change.blockId ?? null,
        },
      ].slice(-300)
    }
    set((s) => ({ exam: draft, past: [...s.past, exam].slice(-HISTORY_LIMIT), future: [], saveState: 'dirty' }))
    backup(project, draft, revision)
    scheduleSave(get)
  },

  undo: () => {
    const { past, exam, future, project, revision } = get()
    if (!past.length || !exam) return
    const previous = past[past.length - 1]
    set({ exam: previous, past: past.slice(0, -1), future: [exam, ...future], saveState: 'dirty' })
    backup(project, previous, revision)
    scheduleSave(get)
  },

  redo: () => {
    const { past, exam, future, project, revision } = get()
    if (!future.length || !exam) return
    const next = future[0]
    set({ exam: next, past: [...past, exam], future: future.slice(1), saveState: 'dirty' })
    backup(project, next, revision)
    scheduleSave(get)
  },

  select: (partId) => set({ selectedPartId: partId }),

  save: async () => {
    const { project, exam, revision, saveState } = get()
    if (!project || !exam || saveState === 'conflict') return
    set({ saveState: 'saving' })
    try {
      const res = await api.saveExam(project.id, revision, exam)
      const stillCurrent = get().exam === exam
      set({
        revision: res.revision,
        project: { ...project, exam, revision: res.revision },
        saveState: stillCurrent ? 'saved' : 'dirty',
        saveError: null,
      })
      if (stillCurrent) clearDraft(project.id)
      else scheduleSave(get)
    } catch (err) {
      const conflict = err instanceof ApiError && err.status === 409
      set({
        saveState: conflict ? 'conflict' : 'error',
        saveError: err instanceof Error ? err.message : 'Erro ao salvar',
      })
      if (!conflict) scheduleSave(get, 5000)
    }
  },
}))

function scheduleSave(get: () => ExamStore, delay = 900) {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    const state = get()
    if (state.saveState === 'saving') scheduleSave(get, 400)
    else void state.save()
  }, delay)
}

/** Resolve once everything typed so far is stored on the server. */
export async function flushSave(): Promise<boolean> {
  clearTimeout(saveTimer)
  const state = useExamStore.getState()
  if (state.saveState === 'dirty' || state.saveState === 'error') await state.save()
  for (let i = 0; i < 50 && useExamStore.getState().saveState === 'saving'; i++) {
    await new Promise((r) => setTimeout(r, 100))
  }
  return useExamStore.getState().saveState === 'saved'
}
