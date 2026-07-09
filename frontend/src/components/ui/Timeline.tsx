// ============================================================
// MekongAI Nexus - Timeline UI Primitive
// Vertical timeline with status-colored dots, icons, and timestamps
// ============================================================

import { type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface TimelineItem {
  /** Unique identifier */
  id: string
  /** Optional icon to display instead of dot */
  icon?: ReactNode
  /** Title text */
  title: string
  /** Optional description */
  description?: string
  /** Optional timestamp string */
  timestamp?: string
  /** Status determines color */
  status?: 'active' | 'done' | 'error' | 'pending'
}

export interface TimelineProps {
  /** Timeline items to render */
  items: TimelineItem[]
  /** Additional class names */
  className?: string
}

const statusDotStyles: Record<NonNullable<TimelineItem['status']>, string> = {
  active: 'bg-[#3b82f6] ring-[#3b82f6]/20 dark:bg-[#60a5fa] dark:ring-[#60a5fa]/20',
  done: 'bg-emerald-500 ring-emerald-500/20 dark:bg-emerald-400 dark:ring-emerald-400/20',
  error: 'bg-red-500 ring-red-500/20 dark:bg-red-400 dark:ring-red-400/20',
  pending: 'bg-[#8e8e93] ring-[#8e8e93]/20 dark:bg-gray-500 dark:ring-gray-500/20',
}

const statusIconStyles: Record<NonNullable<TimelineItem['status']>, string> = {
  active: 'bg-[#3b82f6]/10 text-[#3b82f6] ring-[#3b82f6]/20 dark:bg-[#60a5fa]/10 dark:text-[#60a5fa] dark:ring-[#60a5fa]/20',
  done: 'bg-emerald-500/10 text-emerald-600 ring-emerald-500/20 dark:bg-emerald-400/10 dark:text-emerald-400 dark:ring-emerald-400/20',
  error: 'bg-red-500/10 text-red-600 ring-red-500/20 dark:bg-red-400/10 dark:text-red-400 dark:ring-red-400/20',
  pending: 'bg-gray-100 text-[#8e8e93] ring-gray-200 dark:bg-gray-800 dark:text-gray-500 dark:ring-gray-700',
}

const statusLineStyles: Record<NonNullable<TimelineItem['status']>, string> = {
  active: 'bg-[#3b82f6]/30 dark:bg-[#60a5fa]/30',
  done: 'bg-emerald-500/30 dark:bg-emerald-400/30',
  error: 'bg-red-500/30 dark:bg-red-400/30',
  pending: 'bg-[#e5e7eb] dark:bg-[#2a2a45]',
}

export function Timeline({ items, className }: TimelineProps) {
  if (items.length === 0) return null

  return (
    <div className={cn('relative', className)} role="list" aria-label="Timeline">
      {items.map((item, index) => {
        const status = item.status || 'pending'
        const isLast = index === items.length - 1
        const hasIcon = !!item.icon

        return (
          <div
            key={item.id}
            role="listitem"
            className={cn(
              'relative flex gap-3 pb-6 last:pb-0',
              // Entrance animation
              'animate-in fade-in slide-in-from-left-2 duration-300',
            )}
            style={{ animationDelay: `${index * 75}ms` }}
          >
            {/* Left column: dot/icon + connecting line */}
            <div className="relative flex flex-col items-center">
              {/* Dot or Icon */}
              {hasIcon ? (
                <span
                  className={cn(
                    'relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ring-2',
                    statusIconStyles[status],
                    '[&>svg]:h-4 [&>svg]:w-4',
                    status === 'active' && 'animate-pulse',
                  )}
                  aria-hidden="true"
                >
                  {item.icon}
                </span>
              ) : (
                <span
                  className={cn(
                    'relative z-10 mt-1.5 h-3 w-3 shrink-0 rounded-full ring-4',
                    statusDotStyles[status],
                    status === 'active' && 'animate-pulse',
                  )}
                  aria-hidden="true"
                />
              )}

              {/* Connecting line */}
              {!isLast && (
                <span
                  className={cn(
                    'absolute w-0.5 flex-1',
                    statusLineStyles[status],
                    hasIcon ? 'top-8' : 'top-[18px]',
                    'bottom-0',
                  )}
                  aria-hidden="true"
                />
              )}
            </div>

            {/* Right column: content */}
            <div className={cn('min-w-0 flex-1', hasIcon ? 'pt-1' : 'pt-0')}>
              <div className="flex items-start justify-between gap-2">
                <p
                  className={cn(
                    'text-sm font-medium leading-tight',
                    status === 'pending'
                      ? 'text-[#8e8e93] dark:text-gray-500'
                      : 'text-[#222] dark:text-gray-100',
                  )}
                >
                  {item.title}
                </p>
                {item.timestamp && (
                  <time
                    className="shrink-0 text-xs text-[#8e8e93] dark:text-gray-500"
                    dateTime={item.timestamp}
                  >
                    {item.timestamp}
                  </time>
                )}
              </div>
              {item.description && (
                <p className="mt-0.5 text-xs leading-relaxed text-[#45515e] dark:text-gray-400">
                  {item.description}
                </p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
