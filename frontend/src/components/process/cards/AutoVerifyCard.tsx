// ============================================================
// Nexus - AutoVerifyCard
// Displays tool output verification results with pass/fail/warning
// status, issues, and fix suggestions.
// ============================================================

import { motion } from 'framer-motion'
import { Search, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import type { AutoVerifyResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface AutoVerifyCardProps {
  data: AutoVerifyResult
}

// ── Helpers ───────────────────────────────────────────────────

const RESULT_CONFIG: Record<string, {
  icon: typeof CheckCircle2
  variant: 'success' | 'warning' | 'error'
  label: string
  iconColor: string
  borderColor: string
  bgColor: string
}> = {
  pass: {
    icon: CheckCircle2,
    variant: 'success',
    label: 'Pass',
    iconColor: 'text-emerald-600 dark:text-emerald-400',
    borderColor: 'border-emerald-200/80 dark:border-emerald-800/60',
    bgColor: 'bg-gradient-to-r from-emerald-50/80 to-green-50/80 dark:from-emerald-900/20 dark:to-green-900/20',
  },
  fail: {
    icon: XCircle,
    variant: 'error',
    label: 'Fail',
    iconColor: 'text-red-600 dark:text-red-400',
    borderColor: 'border-red-200/80 dark:border-red-800/60',
    bgColor: 'bg-gradient-to-r from-red-50/80 to-rose-50/80 dark:from-red-900/20 dark:to-rose-900/20',
  },
  warning: {
    icon: AlertTriangle,
    variant: 'warning',
    label: 'Warning',
    iconColor: 'text-amber-600 dark:text-amber-400',
    borderColor: 'border-amber-200/80 dark:border-amber-800/60',
    bgColor: 'bg-gradient-to-r from-amber-50/80 to-yellow-50/80 dark:from-amber-900/20 dark:to-yellow-900/20',
  },
}

// ── Component ─────────────────────────────────────────────────

export function AutoVerifyCard({ data }: AutoVerifyCardProps) {
  const result = data.verification_result || 'pass'
  const config = RESULT_CONFIG[result] || RESULT_CONFIG.pass
  const ResultIcon = config.icon

  const issues = Array.isArray(data.issues) ? data.issues : []
  const fixSuggestions = Array.isArray(data.fix_suggestions) ? data.fix_suggestions : []
  const hasIssues = issues.length > 0
  const hasSuggestions = fixSuggestions.length > 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border overflow-hidden',
        config.bgColor,
        config.borderColor,
      )}
    >
      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <Search className="h-4 w-4 text-gray-600 dark:text-gray-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Auto-Verify
          </span>
          {data.tool_name && (
            <span className={cn(
              'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium',
              'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
            )}>
              {data.tool_name}
            </span>
          )}
          <Badge variant={config.variant} size="sm" className="ml-auto">
            <ResultIcon className="h-3 w-3 mr-0.5" />
            {config.label}
          </Badge>
        </div>

        {/* Issues */}
        {hasIssues && (
          <div className="mt-2">
            <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
              Issues:
            </span>
            <ul className="mt-0.5 pl-3 space-y-0.5">
              {issues.map((issue, idx) => (
                <li
                  key={idx}
                  className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
                >
                  <span className="text-red-400 dark:text-red-500 mt-0.5 shrink-0">•</span>
                  <span>{issue}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Fix Suggestions */}
        {hasSuggestions && (
          <div className="mt-1.5">
            <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
              Fix Suggestions:
            </span>
            <ul className="mt-0.5 pl-3 space-y-0.5">
              {fixSuggestions.map((suggestion, idx) => (
                <li
                  key={idx}
                  className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
                >
                  <span className="text-amber-400 dark:text-amber-500 mt-0.5 shrink-0">💡</span>
                  <span>{suggestion}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </motion.div>
  )
}
