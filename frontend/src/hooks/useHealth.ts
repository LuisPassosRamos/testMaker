import { create } from 'zustand'
import { api } from '../api/client'
import type { FontOption, Health } from '../api/types'

interface SystemStore {
  health: Health | null
  fonts: FontOption[]
  loaded: boolean
  load: () => Promise<void>
}

/** Capabilities of this installation (OCR, PDF converter, AI, e-mail mode), loaded once. */
export const useSystem = create<SystemStore>((set, get) => ({
  health: null,
  fonts: [],
  loaded: false,
  load: async () => {
    if (get().loaded) return
    set({ loaded: true })
    try {
      const [health, fonts] = await Promise.all([api.health(), api.fonts()])
      set({ health, fonts })
    } catch {
      set({ loaded: false })
    }
  },
}))
