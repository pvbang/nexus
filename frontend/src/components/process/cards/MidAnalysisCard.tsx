// ============================================================
// Nexus - MidAnalysisCard
// Displays mid-execution checkpoint with strategy adjustments
// and new insights discovered during processing.
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { BarChart3, ChevronDown, ChevronRight, CheckCircle2, XCircle } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import type { MidAnalysisResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface MidAnalysisCardProps {
  data: MidAnalysisResult
}

// ── Component ─────────────────────────────────────────────────

export function MidAnalysisCard({ data }: MidAnalysisCardProps) {
  const [expanded, setExpanded] = useState(false)

  const onTrack = data.on_track ?? true
  const newInsights = Array.isArray(data.new_insights) ? data.new_insights : []
  const hasInsights = newInsights.length > 0
  const hasStrategyAdjustment = !!data.strategy_adjustment
  const hasDetails = hasInsights || hasStrategyAdjustment

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border overflow-hidden',
        'bg-gradient-to-r from-sky-50/80 to-indigo-50/80 border-sky-200/80',
        'dark:from-sky-900/20 dark:to-indigo-900/20 dark:border-sky-800/60',
      )}
    >
      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-sky-600 dark:text-sky-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Mid-Analysis
          </span>
          {data.round != null && (
            <span className="text-[11px] text-sky-500 dark:text-sky-400 font-medium">
              Round {data.round}
            </span>
          )}
          {onTrack ? (
            <Badge variant="success" size="sm">
              <CheckCircle2 className="h-3 w-3 mr-0.5" />
              On Track
            </Badge>
          ) : (
            <Badge variant="error" size="sm">
              <XCircle className="h-3 w-3 mr-0.5" />
              Off Track
            </Badge>
          )}
          {hasDetails && (
            <button
              type="button"
              onClick={() => setExpanded((p) => !p)}
              className="ml-auto p-1 rounded hover:bg-sky-100 dark:hover:bg-sky-900/40 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
              aria-label={expanded ? 'Collapse details' : 'Expand details'}
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>

        {/* Strategy Adjustment — always visible if present */}
        {hasStrategyAdjustment && (
          <p className="mt-1.5 text-[12px] text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line">
            <span className="font-semibold text-sky-700 dark:text-sky-400">Strategy Adjustment: </span>
            {data.strategy_adjustment}
          </p>
        )}

        {/* Expanded: New Insights */}
        {expanded && hasInsights && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2 }}
            className="mt-2"
          >
            <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
              New Insights:
            </span>
            <ul className="mt-0.5 pl-3 space-y-0.5">
              {newInsights.map((insight, idx) => (
                <li
                  key={idx}
                  className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
                >
                  <span className="text-sky-400 dark:text-sky-500 mt-0.5 shrink-0">💡</span>
                  <span>{insight}</span>
                </li>
              ))}
            </ul>
          </motion.div>
        )}

        {/* Collapsed summary */}
        {!expanded && hasInsights && (
          <div className="mt-1 text-[10px] text-gray-500 dark:text-gray-500">
            💡 {newInsights.length} new insight{newInsights.length > 1 ? 's' : ''}
          </div>
        )}
      </div>
    </motion.div>
  )
}
