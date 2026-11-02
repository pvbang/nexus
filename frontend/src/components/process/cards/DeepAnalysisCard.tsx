// ============================================================
// Nexus - DeepAnalysisCard
// Displays Deep Analysis Engine results with dimensions, risks,
// assumptions, unknowns, tool plan, and success criteria.
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import {
  Microscope,
  ChevronDown,
  ChevronRight,
  Ruler,
  AlertTriangle,
  Lightbulb,
  HelpCircle,
  Wrench,
  CheckCircle2,
} from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import type { DeepAnalysisResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface DeepAnalysisCardProps {
  data: DeepAnalysisResult
}

// ── Helpers ───────────────────────────────────────────────────

const COMPLEXITY_CONFIG: Record<string, { label: string; variant: 'success' | 'warning' | 'error' | 'purple'; className: string }> = {
  low: { label: 'Low', variant: 'success', className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
  medium: { label: 'Medium', variant: 'warning', className: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400' },
  high: { label: 'High', variant: 'error', className: 'bg-orange-50 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' },
  very_high: { label: 'Very High', variant: 'error', className: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
}

function CollapsibleSection({
  icon,
  title,
  items,
  iconColor,
  defaultExpanded = false,
}: {
  icon: React.ReactNode
  title: string
  items: string[]
  iconColor: string
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  if (items.length === 0) return null

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setExpanded((p) => !p)}
        className="flex items-center gap-1.5 w-full text-left group"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 text-gray-400 shrink-0" />
        )}
        <span className={cn('shrink-0', iconColor)}>{icon}</span>
        <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
          {title}
        </span>
        <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-auto">
          {items.length}
        </span>
      </button>
      {expanded && (
        <motion.ul
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.15 }}
          className="mt-1 pl-6 space-y-0.5"
        >
          {items.map((item, idx) => (
            <li
              key={idx}
              className="text-[11px] text-gray-600 dark:text-gray-400 leading-relaxed flex items-start gap-1.5"
            >
              <span className="text-gray-300 dark:text-gray-600 mt-1 shrink-0">•</span>
              <span>{item}</span>
            </li>
          ))}
        </motion.ul>
      )}
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────

// Defensive: backend may send non-array values for array fields
const safeArr = (val: unknown): string[] =>
  Array.isArray(val) ? (val as string[]) : []

// Format strategy text: insert line breaks before numbered steps like "1)", "2)", etc.
function formatStrategy(text: string): string {
  // Insert newline before patterns like "2)", "3)", ... (not before "1)" if it starts the string)
  return text.replace(/(?<=\S)\s*(\d+\))/g, '\n$1')
}

export function DeepAnalysisCard({ data }: DeepAnalysisCardProps) {
  const [expanded, setExpanded] = useState(false)

  const complexity = data.complexity_estimate || 'medium'
  const complexityConfig = COMPLEXITY_CONFIG[complexity] || COMPLEXITY_CONFIG.medium

  // Use safeArr to prevent "not iterable" crashes when backend sends number/string
  const dimensions = safeArr(data.dimensions)
  const risks = safeArr(data.risks)
  const assumptions = safeArr(data.assumptions)
  const unknowns = safeArr(data.unknowns)
  const toolPlan = safeArr(data.tool_plan)
  const successCriteria = safeArr(data.success_criteria)

  const hasDimensions = dimensions.length > 0
  const hasRisks = risks.length > 0
  const hasAssumptions = assumptions.length > 0
  const hasUnknowns = unknowns.length > 0
  const hasToolPlan = toolPlan.length > 0
  const hasSuccessCriteria = successCriteria.length > 0
  const hasDetails = hasDimensions || hasRisks || hasAssumptions || hasUnknowns || hasToolPlan || hasSuccessCriteria

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
        'bg-gradient-to-r from-cyan-50/80 to-blue-50/80 border-cyan-200/80',
        'dark:from-cyan-900/20 dark:to-blue-900/20 dark:border-cyan-800/60',
      )}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-cyan-500" />

      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <Microscope className="h-4 w-4 text-cyan-600 dark:text-cyan-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Deep Analysis
          </span>
          <Badge
            variant={complexityConfig.variant}
            size="sm"
            className={complexityConfig.className}
          >
            {complexityConfig.label}
          </Badge>
          {hasDetails && (
            <button
              type="button"
              onClick={() => setExpanded((p) => !p)}
              className="ml-auto p-1 rounded hover:bg-cyan-100 dark:hover:bg-cyan-900/40 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
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

        {/* Strategy — always visible */}
        {data.strategy && (
          <p className="mt-1.5 text-[12px] text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line">
            <span className="font-semibold text-cyan-700 dark:text-cyan-400">Strategy: </span>
            {formatStrategy(data.strategy)}
          </p>
        )}

        {/* Expandable details */}
        {expanded && hasDetails && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2 }}
            className="mt-3 space-y-2"
          >
            {/* Two-column layout for dimensions/risks */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <CollapsibleSection
                icon={<Ruler className="h-3 w-3" />}
                title="Dimensions"
                items={dimensions}
                iconColor="text-blue-500"
                defaultExpanded
              />
              <CollapsibleSection
                icon={<AlertTriangle className="h-3 w-3" />}
                title="Risks"
                items={risks}
                iconColor="text-red-500"
                defaultExpanded
              />
            </div>

            {/* Two-column layout for assumptions/unknowns */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <CollapsibleSection
                icon={<Lightbulb className="h-3 w-3" />}
                title="Assumptions"
                items={assumptions}
                iconColor="text-amber-500"
              />
              <CollapsibleSection
                icon={<HelpCircle className="h-3 w-3" />}
                title="Unknowns"
                items={unknowns}
                iconColor="text-purple-500"
              />
            </div>

            {/* Tool Plan */}
            {hasToolPlan && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <Wrench className="h-3 w-3 text-gray-500 shrink-0" />
                <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                  Tool Plan:
                </span>
                {toolPlan.map((tool, idx) => (
                  <span
                    key={idx}
                    className={cn(
                      'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium',
                      'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
                    )}
                  >
                    {tool}
                  </span>
                ))}
              </div>
            )}

            {/* Success Criteria */}
            {hasSuccessCriteria && (
              <div className="flex items-start gap-1.5">
                <CheckCircle2 className="h-3 w-3 text-emerald-500 shrink-0 mt-0.5" />
                <div>
                  <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                    Success Criteria:
                  </span>
                  <span className="text-[11px] text-gray-600 dark:text-gray-400 ml-1">
                    {successCriteria.join('; ')}
                  </span>
                </div>
              </div>
            )}
          </motion.div>
        )}

        {/* Collapsed summary */}
        {!expanded && hasDetails && (
          <div className="mt-1.5 flex items-center gap-2 flex-wrap text-[10px] text-gray-500 dark:text-gray-500">
            {hasDimensions && <span>📐 {dimensions.length} dimensions</span>}
            {hasRisks && <span>⚠️ {risks.length} risks</span>}
            {hasAssumptions && <span>💡 {assumptions.length} assumptions</span>}
            {hasUnknowns && <span>❓ {unknowns.length} unknowns</span>}
          </div>
        )}
      </div>
    </motion.div>
  )
}
