// ============================================================
// Nexus - SelfReflectionCard
// Displays self-reflection checkpoint results with quality score,
// on-track indicator, missing aspects, and strategy changes.
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { ScanEye, ChevronDown, ChevronRight, CheckCircle2, XCircle } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import { ProgressBar } from '../../ui/ProgressBar'
import type { SelfReflectionResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface SelfReflectionCardProps {
  data: SelfReflectionResult
}

// ── Helpers ───────────────────────────────────────────────────

function getQualityVariant(score: number): 'success' | 'warning' | 'error' {
  if (score >= 0.7) return 'success'
  if (score >= 0.4) return 'warning'
  return 'error'
}

// ── Component ─────────────────────────────────────────────────

export function SelfReflectionCard({ data }: SelfReflectionCardProps) {
  const [expanded, setExpanded] = useState(true)

  const qualityScore = data.quality_score ?? 0
  const qualityPct = Math.round(qualityScore * 100)
  const qualityVariant = getQualityVariant(qualityScore)
  const onTrack = data.on_track ?? true

  const missingAspects = Array.isArray(data.missing_aspects) ? data.missing_aspects : []
  const improvementSuggestions = Array.isArray(data.improvement_suggestions) ? data.improvement_suggestions : []
  const hasMissingAspects = missingAspects.length > 0
  const hasStrategyChange = !!data.strategy_change
  const hasSuggestions = improvementSuggestions.length > 0
  const hasDetails = hasMissingAspects || hasStrategyChange || hasSuggestions

  return (
    <motion.div
      initial={{ opacity: 0, y: 18, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        type: 'spring',
        stiffness: 340,
        damping: 26,
        mass: 0.8,
      }}
      className={cn(
        'rounded-xl border overflow-hidden shadow-sm',
        'bg-gradient-to-r from-violet-50/80 to-purple-50/80 border-violet-200/80',
        'dark:from-violet-900/20 dark:to-purple-900/20 dark:border-violet-800/60',
      )}
    >
      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <ScanEye className="h-4 w-4 text-violet-600 dark:text-violet-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Self-Reflection
          </span>
          {data.round != null && (
            <span className="text-[11px] text-violet-500 dark:text-violet-400 font-medium">
              Round {data.round}
            </span>
          )}
          {/* On Track badge */}
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
              className="ml-auto p-1 rounded hover:bg-violet-100 dark:hover:bg-violet-900/40 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
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

        {/* Quality progress bar — always visible */}
        <div className="mt-2">
          <ProgressBar
            value={qualityPct}
            variant={qualityVariant}
            size="sm"
            label="Quality"
            showValue
          />
        </div>

        {/* Expanded details */}
        {expanded && hasDetails && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2 }}
            className="mt-3 space-y-2"
          >
            {/* Missing Aspects */}
            {hasMissingAspects && (
              <div>
                <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                  Missing Aspects:
                </span>
                <ul className="mt-0.5 pl-3 space-y-0.5">
                  {missingAspects.map((aspect, idx) => (
                    <li
                      key={idx}
                      className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
                    >
                      <span className="text-violet-400 dark:text-violet-500 mt-0.5 shrink-0">•</span>
                      <span>{aspect}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Strategy Change */}
            {hasStrategyChange && (
              <div>
                <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                  Strategy Change:
                </span>
                <p className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed mt-0.5 whitespace-pre-line">
                  {data.strategy_change}
                </p>
              </div>
            )}

            {/* Suggestions */}
            {hasSuggestions && (
              <div>
                <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                  Suggestions:
                </span>
                <ul className="mt-0.5 pl-3 space-y-0.5">
                  {improvementSuggestions.map((suggestion, idx) => (
                    <li
                      key={idx}
                      className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
                    >
                      <span className="text-violet-400 dark:text-violet-500 mt-0.5 shrink-0">•</span>
                      <span>{suggestion}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </motion.div>
        )}

        {/* Collapsed summary */}
        {!expanded && hasDetails && (
          <div className="mt-1.5 text-[10px] text-gray-500 dark:text-gray-500">
            {hasMissingAspects && (
              <span>🔍 {missingAspects.length} missing aspects</span>
            )}
            {hasStrategyChange && (
              <span className="ml-2">🔄 Strategy adjusted</span>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}
