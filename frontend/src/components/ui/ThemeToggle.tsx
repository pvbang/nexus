// ============================================================
// Nexus - Theme Toggle Component
// Dark/Light/System toggle with smooth transitions
// ============================================================

import { Sun, Moon, Monitor } from 'lucide-react'
import { motion } from 'framer-motion'
import { cn } from '../../lib/cn'
import { usePreferenceStore } from '../../stores/preferenceStore'

type ThemeMode = 'light' | 'dark' | 'system'

const modes: { id: ThemeMode; icon: typeof Sun; label: string }[] = [
  { id: 'light', icon: Sun, label: 'Light' },
  { id: 'dark', icon: Moon, label: 'Dark' },
  { id: 'system', icon: Monitor, label: 'System' },
]

export function ThemeToggle({ className }: { className?: string }) {
  const theme = usePreferenceStore((s) => s.theme)
  const setTheme = usePreferenceStore((s) => s.setTheme)

  return (
    <div className={cn(
      'inline-flex items-center gap-0.5 p-1 rounded-xl',
      'bg-gray-100 dark:bg-[#1a1a2e] border border-gray-200 dark:border-[#2a2a45]',
      className
    )}>
      {modes.map(mode => {
        const Icon = mode.icon
        const isActive = theme === mode.id
        return (
          <button
            key={mode.id}
            onClick={() => setTheme(mode.id)}
            className={cn(
              'relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-colors',
              isActive
                ? 'text-gray-900 dark:text-white'
                : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'
            )}
            title={mode.label}
          >
            {isActive && (
              <motion.div
                layoutId="theme-toggle-bg"
                className="absolute inset-0 bg-white dark:bg-[#252545] rounded-lg shadow-sm border border-gray-200 dark:border-[#3a3a5a]"
                transition={{ type: 'spring', duration: 0.3, bounce: 0.15 }}
              />
            )}
            <span className="relative z-10 flex items-center gap-1.5">
              <Icon className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">{mode.label}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ── Compact version (icon only) ──────────────────────────────

export function ThemeToggleCompact({ className }: { className?: string }) {
  const theme = usePreferenceStore((s) => s.theme)
  const setTheme = usePreferenceStore((s) => s.setTheme)

  const cycle = () => {
    const next: ThemeMode = theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light'
    setTheme(next)
  }

  const Icon = theme === 'light' ? Sun : theme === 'dark' ? Moon : Monitor

  return (
    <button
      onClick={cycle}
      className={cn(
        'w-8 h-8 rounded-lg flex items-center justify-center transition-colors',
        'hover:bg-gray-100 dark:hover:bg-[#1a1a2e]',
        'text-gray-500 dark:text-gray-400',
        className
      )}
      title={`Theme: ${theme}`}
    >
      <Icon className="w-4 h-4" />
    </button>
  )
}
