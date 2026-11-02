// ============================================================
// Nexus - SynthesisCard
// Inline chat card showing synthesis progress and confidence
// ============================================================

import { motion } from 'framer-motion'
import { Puzzle } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import { ProgressBar } from '../../ui/ProgressBar'
import { Spinner } from '../../ui/Spinner'
import type { SynthesisEvent } from '../../../types/process'

interface SynthesisCardProps {
  event: SynthesisEvent
}

export function SynthesisCard({ event }: SynthesisCardProps) {
  const isDone = event.type === 'done'
  const isProgress = event.type === 'progress'

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
      <div className="flex items-center gap-2">
        <Puzzle className="h-4 w-4 text-violet-500 dark:text-violet-400 shrink-0" />
        <span className="font-semibold text-text-primary dark:text-gray-100">
          {isDone ? 'Synthesis Complete' : 'Synthesizing Results'}
        </span>
        {!isDone && <Spinner size="xs" className="text-violet-500" />}
        {isDone && (
          <Badge variant="success" size="sm">done</Badge>
        )}
      </div>

      {/* Sources count */}
      {event.sourcesCount != null && (
        <p className="mt-1.5 text-xs text-text-secondary dark:text-gray-400">
          Combining outputs from {event.sourcesCount} agent{event.sourcesCount !== 1 ? 's' : ''}…
        </p>
      )}

      {/* Progress bar (when in progress) */}
      {isProgress && event.confidence != null && (
        <div className="mt-2">
          <ProgressBar
            value={event.confidence * 100}
            size="sm"
            animated
          />
        </div>
      )}

      {/* Confidence display (when done) */}
      {isDone && event.confidence != null && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-xs text-text-muted dark:text-gray-500">Confidence:</span>
          <ProgressBar
            value={event.confidence * 100}
            variant="success"
            size="sm"
            showValue
            className="flex-1"
          />
        </div>
      )}

      {/* Summary preview */}
      {event.summary && (
        <p className="mt-1.5 text-xs text-text-secondary dark:text-gray-400 line-clamp-2 leading-relaxed">
          {event.summary}
        </p>
      )}
    </motion.div>
  )
}
