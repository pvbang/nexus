// ============================================================
// MekongAI Nexus - Language Switcher Component
// Feature #9: Toggle between Vietnamese and English
// ============================================================

import { useTranslation } from './context'
import { cn } from '../lib/cn'
import type { Locale } from './types'

const LOCALE_OPTIONS: { locale: Locale; flag: string; label: string }[] = [
  { locale: 'vi', flag: '🇻🇳', label: 'VI' },
  { locale: 'en', flag: '🇺🇸', label: 'EN' },
]

export function LanguageSwitcher() {
  const { locale, setLocale } = useTranslation()

  return (
    <div className="flex items-center gap-1 p-[3px] rounded-[10px] bg-[#f4f5f7] dark:bg-[#2a2a45]">
      {LOCALE_OPTIONS.map((opt) => {
        const isActive = locale === opt.locale
        return (
          <button
            key={opt.locale}
            type="button"
            onClick={() => setLocale(opt.locale)}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-[8px] text-[13px] font-medium transition-all duration-200',
              isActive
                ? [
                    'bg-white text-text-dark shadow-[0_1px_3px_rgba(0,0,0,0.08)]',
                    'dark:bg-[#353560] dark:text-white dark:shadow-none',
                  ]
                : [
                    'text-text-secondary hover:text-text-dark',
                    'dark:text-gray-400 dark:hover:text-gray-200',
                  ],
            )}
          >
            <span className="text-[15px] leading-none">{opt.flag}</span>
            <span>{opt.label}</span>
          </button>
        )
      })}
    </div>
  )
}
