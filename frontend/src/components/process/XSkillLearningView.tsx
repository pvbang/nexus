// ============================================================
// Nexus - XSkillLearningView
// Displays XSkill learning events (retrieve / accumulate / critique)
// ============================================================

import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Brain,
  BookOpen,
  Star,
  ChevronDown,
  Sparkles,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { Badge } from '../ui/Badge'
import { ProgressBar } from '../ui/ProgressBar'
import { EmptyState } from '../ui/EmptyState'
import type { XSkillEvent } from '../../types/process'

// ── Event type config ─────────────────────────────────────────

const eventTypeConfig: Record<
  XSkillEvent['type'],
  {
    icon: typeof Brain
    label: string
    color: string
    bgColor: string
  }
> = {
  retrieve: {
    icon: BookOpen,
    label: 'Retrieved skill',
    color: 'text-blue-600 dark:text-blue-400',
    bgColor: 'bg-blue-100 dark:bg-blue-900/30',
  },
  accumulate: {
    icon: Brain,
    label: 'Accumulated experience',
    color: 'text-purple-600 dark:text-purple-400',
    bgColor: 'bg-purple-100 dark:bg-purple-900/30',
  },
  critique: {
    icon: Star,
    label: 'Critique score',
    color: 'text-amber-600 dark:text-amber-400',
    bgColor: 'bg-amber-100 dark:bg-amber-900/30',
  },
}

// ── Single event card ─────────────────────────────────────────

function XSkillEventCard({ event }: { event: XSkillEvent }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = eventTypeConfig[event.type]
  const Icon = cfg.icon
  const hasDetails = !!event.details

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.2 }}
      className={cn(
        'rounded-lg border p-3',
        'border-border dark:border-[#2a2a45]',
        'bg-white dark:bg-[#1a1a2e]',
      )}
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <span
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
            cfg.bgColor,
          )}
        >
          <Icon className={cn('h-4 w-4', cfg.color)} aria-hidden />
        </span>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <span className={cn('text-xs font-semibold', cfg.color)}>
              {cfg.label}
            </span>
            {event.skillName && (
              <Badge variant="brand" size="sm">
                {event.skillName}
              </Badge>
            )}
            {event.type === 'critique' && event.quality != null && (
              <Badge
                variant={
                  event.quality >= 7
                    ? 'success'
                    : event.quality >= 4
                      ? 'warning'
                      : 'error'
                }
                size="sm"
              >
                {event.quality}/10
              </Badge>
            )}
          </div>

          {/* Experience count */}
          {event.experienceCount != null && (
            <p className="text-xs text-text-secondary dark:text-gray-400">
              {event.experienceCount} experience{event.experienceCount !== 1 ? 's' : ''} collected
            </p>
          )}

          {/* Quality bar for critique */}
          {event.type === 'critique' && event.quality != null && (
            <div className="mt-2">
              <ProgressBar
                value={event.quality * 10}
                size="sm"
                variant={
                  event.quality >= 7
                    ? 'success'
                    : event.quality >= 4
                      ? 'warning'
                      : 'error'
                }
                label="Quality"
                showValue
              />
            </div>
          )}

          {/* Expandable details */}
          {hasDetails && (
            <>
              <button
                type="button"
                onClick={() => setExpanded((p) => !p)}
                className={cn(
                  'mt-1.5 inline-flex items-center gap-0.5 text-xs font-medium',
                  'text-brand-500 hover:text-brand-600 dark:text-brand-400',
                  'transition-colors',
                )}
              >
                {expanded ? 'Hide details' : 'Show details'}
                <ChevronDown
                  className={cn(
                    'h-3 w-3 transition-transform duration-200',
                    expanded && 'rotate-180',
                  )}
                  aria-hidden
                />
              </button>
              <AnimatePresence initial={false}>
                {expanded && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2, ease: 'easeInOut' }}
                    className="overflow-hidden"
                  >
                    <p className="mt-2 text-xs text-text-secondary dark:text-gray-400 leading-relaxed whitespace-pre-wrap">
                      {event.details}
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>
            </>
          )}
        </div>

        {/* Timestamp */}
        <span className="shrink-0 text-[10px] text-text-muted dark:text-gray-600 tabular-nums">
          {new Date(event.timestamp).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          })}
        </span>
      </div>
    </motion.div>
  )
}

// ── Main component ────────────────────────────────────────────

export function XSkillLearningView() {
  const xskillEvents = useProcessStore((s) => s.xskillEvents)

  if (xskillEvents.length === 0) {
    return (
      <EmptyState
        icon={<Sparkles className="h-10 w-10" />}
        title="No learning events"
        description="XSkill events will appear here when the system retrieves or builds skills."
      />
    )
  }

  return (
    <div className="space-y-2 animate-fade-in">
      <div className="flex items-center gap-2 mb-1">
        <Brain className="h-4 w-4 text-purple-500 dark:text-purple-400" aria-hidden />
        <span className="text-xs text-text-muted dark:text-gray-500">
          {xskillEvents.length} event{xskillEvents.length !== 1 ? 's' : ''}
        </span>
      </div>
      <AnimatePresence initial={false}>
        {xskillEvents.map((event) => (
          <XSkillEventCard key={event.id} event={event} />
        ))}
      </AnimatePresence>
    </div>
  )
}
