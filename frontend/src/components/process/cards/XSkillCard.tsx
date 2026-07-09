// ============================================================
// MekongAI Nexus - XSkillCard
// Inline chat card showing XSkill learning events
// ============================================================

import { motion } from 'framer-motion'
import { Brain } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import { ProgressBar } from '../../ui/ProgressBar'
import type { XSkillEvent } from '../../../types/process'

interface XSkillCardProps {
  events: XSkillEvent[]
}

const TYPE_LABELS: Record<XSkillEvent['type'], string> = {
  retrieve: 'Retrieved',
  accumulate: 'Accumulated',
  critique: 'Critiqued',
}

const TYPE_VARIANTS: Record<XSkillEvent['type'], 'info' | 'purple' | 'warning'> = {
  retrieve: 'info',
  accumulate: 'purple',
  critique: 'warning',
}

export function XSkillCard({ events }: XSkillCardProps) {
  if (events.length === 0) return null

  // Aggregate stats from events
  const totalExperiences = events.reduce(
    (sum, e) => sum + (e.experienceCount ?? 0),
    0,
  )
  const latestQuality = events
    .filter((e) => e.quality != null)
    .at(-1)?.quality

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border border-border bg-white px-3.5 py-2.5 text-sm',
        'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <Brain className="h-4 w-4 text-purple-500 dark:text-purple-400 shrink-0" />
        <span className="font-semibold text-text-primary dark:text-gray-100">
          Learning
        </span>
      </div>

      {/* Event list */}
      <div className="space-y-1.5">
        {events.map((event) => (
          <div key={event.id} className="flex items-center gap-2 text-xs">
            <Badge variant={TYPE_VARIANTS[event.type]} size="sm">
              {TYPE_LABELS[event.type]}
            </Badge>
            {event.skillName && (
              <span className="font-mono text-text-secondary dark:text-gray-400 truncate">
                &quot;{event.skillName}&quot;
              </span>
            )}
            {event.experienceCount != null && (
              <span className="text-text-muted dark:text-gray-500 ml-auto shrink-0">
                {event.experienceCount} exp
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Aggregated footer */}
      <div className="mt-2 flex items-center gap-3 text-xs text-text-muted dark:text-gray-500">
        {totalExperiences > 0 && (
          <span>Total: {totalExperiences} experiences</span>
        )}
      </div>

      {/* Quality bar */}
      {latestQuality != null && (
        <div className="mt-1.5 flex items-center gap-2">
          <span className="text-xs text-text-muted dark:text-gray-500 shrink-0">
            Quality:
          </span>
          <ProgressBar
            value={latestQuality * 10}
            size="sm"
            variant={latestQuality >= 7 ? 'success' : latestQuality >= 4 ? 'warning' : 'error'}
            className="flex-1"
          />
          <span className="text-xs font-medium text-text-secondary dark:text-gray-400 tabular-nums shrink-0">
            {latestQuality.toFixed(1)}/10
          </span>
        </div>
      )}
    </motion.div>
  )
}
