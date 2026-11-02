// ============================================================
// Nexus - SynthesisView
// Displays final-answer synthesis progress and results
// ============================================================

import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { Layers, FileText, Gauge } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { Spinner } from '../ui/Spinner'
import { ProgressBar } from '../ui/ProgressBar'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/EmptyState'

export function SynthesisView() {
  const synthesisEvents = useProcessStore((s) => s.synthesisEvents)

  // Derive state from events
  const state = useMemo(() => {
    if (synthesisEvents.length === 0) return null

    const latest = synthesisEvents[synthesisEvents.length - 1]
    const startEvent = synthesisEvents.find((e) => e.type === 'start')
    const doneEvent = synthesisEvents.find((e) => e.type === 'done')

    return {
      isActive: latest.type !== 'done',
      isDone: !!doneEvent,
      sourcesCount: startEvent?.sourcesCount ?? doneEvent?.sourcesCount ?? 0,
      summary: doneEvent?.summary ?? null,
      confidence: doneEvent?.confidence ?? latest.confidence ?? null,
    }
  }, [synthesisEvents])

  if (!state) {
    return (
      <EmptyState
        icon={<Layers className="h-10 w-10" />}
        title="No synthesis activity"
        description="Synthesis events will appear here when the system combines agent results."
      />
    )
  }

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Status header */}
      <div className="flex items-center gap-3">
        {state.isActive ? (
          <>
            <Spinner size="sm" className="text-brand-500 dark:text-brand-400" />
            <span className="text-sm font-medium text-text-primary dark:text-gray-100">
              Synthesizing from {state.sourcesCount} source
              {state.sourcesCount !== 1 ? 's' : ''}…
            </span>
          </>
        ) : (
          <>
            <span
              className={cn(
                'flex h-7 w-7 items-center justify-center rounded-full',
                'bg-emerald-100 dark:bg-emerald-900/30',
              )}
            >
              <Layers className="h-4 w-4 text-emerald-600 dark:text-emerald-400" aria-hidden />
            </span>
            <span className="text-sm font-medium text-emerald-700 dark:text-emerald-400">
              Synthesis complete
            </span>
            {state.sourcesCount > 0 && (
              <Badge variant="default" size="sm">
                <FileText className="mr-0.5 h-3 w-3 inline" aria-hidden />
                {state.sourcesCount} sources
              </Badge>
            )}
          </>
        )}
      </div>

      {/* Confidence meter */}
      {state.confidence != null && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="flex items-center gap-2 mb-1.5">
            <Gauge className="h-3.5 w-3.5 text-text-muted dark:text-gray-500" aria-hidden />
            <span className="text-xs font-medium text-text-secondary dark:text-gray-400">
              Confidence
            </span>
          </div>
          <ProgressBar
            value={state.confidence}
            variant={
              state.confidence >= 70
                ? 'success'
                : state.confidence >= 40
                  ? 'warning'
                  : 'error'
            }
            showValue
            animated={state.isActive}
          />
        </motion.div>
      )}

      {/* Summary */}
      {state.summary && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          className={cn(
            'rounded-lg border p-4',
            'border-emerald-200 bg-emerald-50/40',
            'dark:border-emerald-800 dark:bg-emerald-900/10',
          )}
        >
          <p className="text-sm text-text-primary dark:text-gray-200 leading-relaxed whitespace-pre-wrap">
            {state.summary}
          </p>
        </motion.div>
      )}
    </div>
  )
}
