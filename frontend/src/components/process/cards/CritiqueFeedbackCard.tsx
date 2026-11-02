// ============================================================
// Nexus - CritiqueFeedbackCard
// Displays CriticAgent cross-critique results with severity-coded
// critique points and missing perspectives.
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Target, ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import type { CritiqueResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface CritiqueFeedbackCardProps {
  data: CritiqueResult
}

// ── Helpers ───────────────────────────────────────────────────

const SEVERITY_CONFIG: Record<string, { icon: string; color: string; bgColor: string }> = {
  critical: {
    icon: '🔴',
    color: 'text-red-700 dark:text-red-400',
    bgColor: 'bg-red-50 dark:bg-red-900/20',
  },
  warning: {
    icon: '🟡',
    color: 'text-amber-700 dark:text-amber-400',
    bgColor: 'bg-amber-50 dark:bg-amber-900/20',
  },
  info: {
    icon: '🟢',
    color: 'text-emerald-700 dark:text-emerald-400',
    bgColor: 'bg-emerald-50 dark:bg-emerald-900/20',
  },
}

function getQualityBadge(quality: number): { variant: 'success' | 'warning' | 'error'; label: string } {
  if (quality >= 7) return { variant: 'success', label: `${quality}/10` }
  if (quality >= 4) return { variant: 'warning', label: `${quality}/10` }
  return { variant: 'error', label: `${quality}/10` }
}

// ── Component ─────────────────────────────────────────────────

export function CritiqueFeedbackCard({ data }: CritiqueFeedbackCardProps) {
  const [expanded, setExpanded] = useState(false)

  const quality = data.overall_quality ?? 5
  const qualityBadge = getQualityBadge(quality)
  const critiquePoints = Array.isArray(data.critique_points) ? data.critique_points : []
  const missingPerspectives = Array.isArray(data.missing_perspectives) ? data.missing_perspectives : []
  const hasCritiquePoints = critiquePoints.length > 0
  const hasMissingPerspectives = missingPerspectives.length > 0
  const hasDetails = hasCritiquePoints || hasMissingPerspectives

  // Sort critique points by severity: critical first
  const sortedPoints = [...critiquePoints].sort((a, b) => {
    const order: Record<string, number> = { critical: 0, warning: 1, info: 2 }
    return (order[a.severity] ?? 3) - (order[b.severity] ?? 3)
  })

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border overflow-hidden',
        'bg-gradient-to-r from-rose-50/80 to-orange-50/80 border-rose-200/80',
        'dark:from-rose-900/20 dark:to-orange-900/20 dark:border-rose-800/60',
      )}
    >
      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <Target className="h-4 w-4 text-rose-600 dark:text-rose-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Critic Feedback
          </span>
          {data.agent_name && (
            <span className="text-[11px] text-rose-500 dark:text-rose-400 font-medium">
              {data.agent_name}
            </span>
          )}
          <Badge variant={qualityBadge.variant} size="sm" className="ml-auto">
            Quality: {qualityBadge.label}
          </Badge>
          {hasDetails && (
            <button
              type="button"
              onClick={() => setExpanded((p) => !p)}
              className="p-1 rounded hover:bg-rose-100 dark:hover:bg-rose-900/40 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
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

        {/* Expanded: critique points */}
        {expanded && hasCritiquePoints && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2 }}
            className="mt-2.5 space-y-1.5"
          >
            <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
              Critique Points:
            </span>
            {sortedPoints.map((point, idx) => {
              const config = SEVERITY_CONFIG[point.severity] || SEVERITY_CONFIG.info
              return (
                <div
                  key={idx}
                  className={cn(
                    'flex items-start gap-2 px-2.5 py-1.5 rounded-md text-[11px] leading-relaxed',
                    config.bgColor,
                  )}
                >
                  <span className="shrink-0 mt-0.5">{config.icon}</span>
                  <span className={config.color}>
                    <span className="font-semibold">[{point.severity}]</span> {point.point}
                  </span>
                </div>
              )
            })}
          </motion.div>
        )}

        {/* Expanded: missing perspectives */}
        {expanded && hasMissingPerspectives && (
          <div className="mt-2">
            <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
              Missing Perspectives:
            </span>
            <div className="mt-0.5 flex flex-wrap gap-1">
              {missingPerspectives.map((perspective, idx) => (
                <span
                  key={idx}
                  className={cn(
                    'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium',
                    'bg-rose-100 text-rose-600 dark:bg-rose-800/40 dark:text-rose-400',
                  )}
                >
                  {perspective}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Collapsed summary */}
        {!expanded && hasCritiquePoints && (
          <div className="mt-1.5 text-[10px] text-gray-500 dark:text-gray-500">
            {sortedPoints.slice(0, 2).map((point, idx) => {
              const config = SEVERITY_CONFIG[point.severity] || SEVERITY_CONFIG.info
              return (
                <span key={idx} className="mr-2">
                  {config.icon} {point.point.slice(0, 50)}{point.point.length > 50 ? '…' : ''}
                </span>
              )
            })}
            {sortedPoints.length > 2 && (
              <span className="text-gray-400">+{sortedPoints.length - 2} more</span>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}
