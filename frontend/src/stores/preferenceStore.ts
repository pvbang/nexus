// ============================================================
// Nexus - Preference Store (Zustand + localStorage)
// Feature #7: Added themePreset + accentColor support
// Feature #9: Added locale (i18n) support
// ============================================================

import { create } from 'zustand'
import type { ThemePreset } from '../themes/presets'
import { applyThemePreset, resolveThemeMode } from '../themes/ThemeProvider'
import type { Locale } from '../i18n/types'

/** Transparency level for process display */
export type TransparencyLevel = 'minimal' | 'normal' | 'detailed' | 'debug'

/** Display mode for LLM thinking/reasoning blocks */
export type ThinkingDisplayMode = 'hidden' | 'collapsed' | 'expanded'

interface PreferenceState {
  /** Sidebar collapsed */
  sidebarCollapsed: boolean
  /** Right panel (task progress) collapsed */
  rightPanelCollapsed: boolean
  /** Theme: 'light' | 'dark' | 'system' */
  theme: 'light' | 'dark' | 'system'
  /** Theme preset (Feature #7) */
  themePreset: ThemePreset
  /** Custom accent color hex (Feature #7) */
  accentColor: string
  /** Transparency level for process display */
  transparencyLevel: TransparencyLevel
  /** How to display LLM thinking/reasoning blocks */
  thinkingDisplayMode: ThinkingDisplayMode
  /** UI locale (Feature #9) */
  locale: Locale

  // Actions
  toggleSidebar: () => void
  toggleRightPanel: () => void
  setSidebarCollapsed: (collapsed: boolean) => void
  setRightPanelCollapsed: (collapsed: boolean) => void
  setTheme: (theme: 'light' | 'dark' | 'system') => void
  setThemePreset: (preset: ThemePreset) => void
  setAccentColor: (color: string) => void
  setTransparencyLevel: (level: TransparencyLevel) => void
  setThinkingDisplayMode: (mode: ThinkingDisplayMode) => void
  setLocale: (locale: Locale) => void
  applyTheme: () => void
}

function loadPref<T>(key: string, defaultValue: T): T {
  try {
    const stored = localStorage.getItem(`nexus_pref_${key}`)
    if (stored !== null) return JSON.parse(stored)
  } catch { /* ignore */ }
  return defaultValue
}

function savePref(key: string, value: unknown) {
  try {
    localStorage.setItem(`nexus_pref_${key}`, JSON.stringify(value))
  } catch { /* ignore */ }
}

export const usePreferenceStore = create<PreferenceState>((set, get) => ({
  sidebarCollapsed: loadPref('sidebarCollapsed', false),
  rightPanelCollapsed: loadPref('rightPanelCollapsed', false),
  theme: loadPref('theme', 'light'),
  themePreset: loadPref<ThemePreset>('themePreset', 'default'),
  accentColor: loadPref('accentColor', '#3B82F6'),
  transparencyLevel: loadPref<TransparencyLevel>('transparencyLevel', 'detailed'),
  thinkingDisplayMode: loadPref<ThinkingDisplayMode>('thinkingDisplayMode', 'collapsed'),
  locale: loadPref<Locale>('locale', 'vi'),

  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarCollapsed
      savePref('sidebarCollapsed', next)
      return { sidebarCollapsed: next }
    }),

  toggleRightPanel: () =>
    set((state) => {
      const next = !state.rightPanelCollapsed
      savePref('rightPanelCollapsed', next)
      return { rightPanelCollapsed: next }
    }),

  setSidebarCollapsed: (collapsed) => {
    savePref('sidebarCollapsed', collapsed)
    set({ sidebarCollapsed: collapsed })
  },

  setRightPanelCollapsed: (collapsed) => {
    savePref('rightPanelCollapsed', collapsed)
    set({ rightPanelCollapsed: collapsed })
  },

  setTheme: (theme) => {
    savePref('theme', theme)
    set({ theme })
    // Apply immediately after setting
    get().applyTheme()
  },

  setThemePreset: (preset) => {
    savePref('themePreset', preset)
    set({ themePreset: preset })
    // Apply immediately
    get().applyTheme()
  },

  setAccentColor: (color) => {
    savePref('accentColor', color)
    set({ accentColor: color })
    // Apply immediately
    get().applyTheme()
  },

  setTransparencyLevel: (level) => {
    savePref('transparencyLevel', level)
    set({ transparencyLevel: level })
  },

  setThinkingDisplayMode: (mode) => {
    savePref('thinkingDisplayMode', mode)
    set({ thinkingDisplayMode: mode })
  },

  setLocale: (locale) => {
    savePref('locale', locale)
    set({ locale })
  },

  applyTheme: () => {
    const { theme, themePreset, accentColor } = get()
    const resolvedMode = resolveThemeMode(theme)
    applyThemePreset(
      themePreset,
      resolvedMode,
      themePreset === 'custom' ? accentColor : undefined,
    )
  },
}))
