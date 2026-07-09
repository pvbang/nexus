// ============================================================
// MekongAI Nexus - Tabs UI Primitive
// Pill-style and underline tabs with icon and count support
// ============================================================

import { type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface Tab {
  /** Unique tab identifier */
  id: string
  /** Display label */
  label: string
  /** Optional icon before label */
  icon?: ReactNode
  /** Optional count badge */
  count?: number
}

export interface TabsProps {
  /** Tab definitions */
  tabs: Tab[]
  /** Currently active tab id */
  activeTab: string
  /** Callback when tab changes */
  onChange: (id: string) => void
  /** Visual variant */
  variant?: 'pill' | 'underline'
  /** Tab size */
  size?: 'sm' | 'md'
  /** Additional classes */
  className?: string
}

const containerVariants: Record<NonNullable<TabsProps['variant']>, string> = {
  pill: [
    'inline-flex gap-1 rounded-lg bg-surface-secondary p-1',
    'dark:bg-surface-secondary',
  ].join(' '),
  underline: [
    'inline-flex gap-0 border-b border-border',
    'dark:border-border-subtle',
  ].join(' '),
}

const sizeStyles: Record<NonNullable<TabsProps['size']>, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
}

export function Tabs({
  tabs,
  activeTab,
  onChange,
  variant = 'pill',
  size = 'md',
  className,
}: TabsProps) {
  return (
    <div role="tablist" className={cn(containerVariants[variant], className)}>
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab

        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            aria-controls={`tabpanel-${tab.id}`}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={cn(
              'inline-flex items-center gap-1.5 font-medium transition-all duration-150',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
              sizeStyles[size],

              // Pill variant styles
              variant === 'pill' && [
                'rounded-md',
                isActive
                  ? 'bg-white text-text-primary shadow-subtle dark:bg-surface-tertiary dark:text-white'
                  : 'text-text-muted hover:text-text-secondary dark:text-gray-400 dark:hover:text-gray-300',
              ],

              // Underline variant styles
              variant === 'underline' && [
                'border-b-2 -mb-px rounded-none',
                isActive
                  ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                  : 'border-transparent text-text-muted hover:text-text-secondary hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300 dark:hover:border-gray-600',
              ],
            )}
          >
            {tab.icon && (
              <span className="shrink-0 [&>svg]:h-4 [&>svg]:w-4">{tab.icon}</span>
            )}
            {tab.label}
            {tab.count !== undefined && (
              <span
                className={cn(
                  'ml-0.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full px-1.5 text-[10px] font-semibold leading-none',
                  isActive
                    ? 'bg-brand-100 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300'
                    : 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
