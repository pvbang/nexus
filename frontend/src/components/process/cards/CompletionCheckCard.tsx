// ============================================================
// Nexus - CompletionCheckCard
// Displays completion quality check results with dual progress
// bars for quality and completeness, verdict badge, and missing items.
// ============================================================

import { motion } from 'framer-motion'
import { ClipboardCheck, CheckCircle2, AlertTriangle, XCircle } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import { ProgressBar } from '../../ui/ProgressBar'
import type { CompletionCheckResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface CompletionCheckCardProps {
  data: CompletionCheckResult
}

// ── Helpers ───────────────────────────────────────────────────

const VERDICT_CONFIG: Record<string, {
  icon: typeof CheckCircle2
  variant: 'success' | 'warning' | 'error'
  label: string
}> = {
  pass: { icon: CheckCircle2, variant: 'success', label: 'Pass' },
  needs_improvement: { icon: AlertTriangle, variant: 'warning', label: 'Needs Improvement' },
  fail: { icon: XCircle, variant: 'error', label: 'Fail' },
}

function getScoreVariant(score: number): 'success' | 'warning' | 'error' {
  if (score >= 70) return 'success'
  if (score >= 40) return 'warning'
  return 'error'
}

// ── Component ─────────────────────────────────────────────────

export function CompletionCheckCard({ data }: CompletionCheckCardProps) {
  const verdict = data.verdict || 'pass'
  const verdictConfig = VERDICT_CONFIG[verdict] || VERDICT_CONFIG.pass
  const VerdictIcon = verdictConfig.icon

  const qualityPct = Math.round((data.quality_score ?? 0) * 100)
  const completenessPct = Math.round(data.completeness_pct ?? 0)
  const missingItems = Array.isArray(data.missing_items) ? data.missing_items : []
  const hasMissingItems = missingItems.length > 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border overflow-hidden',
        'bg-gradient-to-r from-teal-50/80 to-emerald-50/80 border-teal-200/80',
        'dark:from-teal-900/20 dark:to-emerald-900/20 dark:border-teal-800/60',
      )}
    >
      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <ClipboardCheck className="h-4 w-4 text-teal-600 dark:text-teal-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Completion Check
          </span>
          <Badge variant={verdictConfig.variant} size="sm" className="ml-auto">
            <VerdictIcon className="h-3 w-3 mr-0.5" />
            {verdictConfig.label}
          </Badge>
        </div>

        {/* Dual progress bars */}
        <div className="mt-2 space-y-1.5">
          <ProgressBar
            value={qualityPct}
            variant={getScoreVariant(qualityPct)}
            size="sm"
            label="Quality"
            showValue
          />
          <ProgressBar
            value={completenessPct}
            variant={getScoreVariant(completenessPct)}
            size="sm"
            label="Completeness"
            showValue
          />
        </div>

        {/* Missing items */}
        {hasMissingItems && (
          <div className="mt-2">
            <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
              Missing Items:
            </span>
            <ul className="mt-0.5 pl-3 space-y-0.5">
              {missingItems.map((item, idx) => (
                <li
                  key={idx}
                  className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
                >
                  <span className="text-amber-400 dark:text-amber-500 mt-0.5 shrink-0">•</span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </motion.div>
  )
}
