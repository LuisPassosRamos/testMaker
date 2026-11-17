// Projects opened in this browser, so a teacher can continue where they stopped.
// Only ids are kept locally; the server never lists projects globally.
const KEY = 'prova-pronta:recent'
const PROFILE = 'prova-pronta:professor'

export function recentIds(): string[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]')
  } catch {
    return []
  }
}

export function rememberProject(id: string) {
  try {
    const ids = [id, ...recentIds().filter((x) => x !== id)].slice(0, 12)
    localStorage.setItem(KEY, JSON.stringify(ids))
  } catch {
    /* ignore */
  }
}

export function forgetProject(id: string) {
  try {
    localStorage.setItem(KEY, JSON.stringify(recentIds().filter((x) => x !== id)))
  } catch {
    /* ignore */
  }
}

export interface TeacherProfile {
  professor: string
  disciplina: string
  email: string
}

export function loadProfile(): TeacherProfile {
  try {
    return { professor: '', disciplina: '', email: '', ...JSON.parse(localStorage.getItem(PROFILE) ?? '{}') }
  } catch {
    return { professor: '', disciplina: '', email: '' }
  }
}

export function saveProfile(profile: Partial<TeacherProfile>) {
  try {
    localStorage.setItem(PROFILE, JSON.stringify({ ...loadProfile(), ...profile }))
  } catch {
    /* ignore */
  }
}
