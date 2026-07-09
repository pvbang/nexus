// ============================================================
// MekongAI Nexus - i18n Context, Provider & Hook
// Feature #9: Type-safe i18n with React Context
// ============================================================

import { createContext, useContext, useMemo, useCallback } from 'react'
import type { Locale, TranslationKey, TranslationKeys } from './types'
import { en } from './en'
import { vi } from './vi'
import { usePreferenceStore } from '../stores/preferenceStore'

// ── Translation dictionaries ──────────────────────────────────
const translations: Record<Locale, TranslationKeys> = { en, vi }

// ── Context value type ────────────────────────────────────────
interface I18nContextValue {
  /** Current locale */
  locale: Locale
  /** Set the active locale */
  setLocale: (locale: Locale) => void
  /** Translate a dot-notation key */
  t: (key: TranslationKey) => string
}

// ── Context ───────────────────────────────────────────────────
const I18nContext = createContext<I18nContextValue | null>(null)

// ── Resolve a dot-notation key from translation object ────────
function resolve(dict: TranslationKeys, key: string): string {
  const parts = key.split('.')
  if (parts.length !== 2) return key

  const [section, field] = parts
  const sectionObj = dict[section as keyof TranslationKeys]
  if (sectionObj === undefined) return key

  const value = (sectionObj as Record<string, string>)[field as string]
  return value ?? key
}

// ── Provider ──────────────────────────────────────────────────
export function I18nProvider({ children }: { children: React.ReactNode }) {
  const locale = usePreferenceStore((s) => s.locale)
  const setLocale = usePreferenceStore((s) => s.setLocale)

  const dict = translations[locale] ?? translations.vi

  const t = useCallback(
    (key: TranslationKey): string => resolve(dict, key),
    [dict],
  )

  const value = useMemo<I18nContextValue>(
    () => ({ locale, setLocale, t }),
    [locale, setLocale, t],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

// ── Hook ──────────────────────────────────────────────────────
export function useTranslation(): I18nContextValue {
  const ctx = useContext(I18nContext)
  if (!ctx) {
    throw new Error('useTranslation must be used within <I18nProvider>')
  }
  return ctx
}
