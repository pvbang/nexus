// ============================================================
// Nexus - PlanningCard
// Inline chat card showing orchestrator plan creation
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { ClipboardList, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import type { ProcessPlan, ProcessPlanStep } from '../../../types/process'

interface PlanningCardProps {
  plan: ProcessPlan
}

const STATUS_STYLES: Record<ProcessPlanStep['status'], string> = {
  pending: 'text-gray-400 dark:text-gray-500',
  running: 'text-blue-500 dark:text-blue-400',
  completed: 'text-emerald-500 dark:text-emerald-400',
  failed: 'text-red-500 dark:text-red-400',
}

const STATUS_LABELS: Record<ProcessPlanStep['status'], string> = {
  pending: 'pending',
  running: 'running…',
  completed: '✓ done',
  failed: '✗ failed',
}

export function PlanningCard({ plan }: PlanningCardProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        type: 'spring',
        stiffness: 360,
        damping: 26,
        mass: 0.8,
      }}
      className={cn(
        'rounded-xl border-l-4 border-l-brand-500 border border-border bg-white',
        'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:border-l-brand-400',
        'px-3.5 py-2.5 text-sm shadow-sm',
        'animate-border-flow',
      )}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded((p) => !p)}
        className="flex w-full items-center gap-2 text-left"
      >
        <ClipboardList className="h-4 w-4 text-brand-500 dark:text-brand-400 shrink-0" />
        <span className="font-semibold text-text-primary dark:text-gray-100">
          Plan Created
        </span>
        <Badge variant="brand" size="sm">{plan.strategy}</Badge>
        <span className="text-xs text-text-muted dark:text-gray-500">
          Steps: {plan.steps?.length ?? 0}
        </span>
        <span className="ml-auto">
          {expanded ? (
            <ChevronUp className="h-3.5 w-3.5 text-text-muted dark:text-gray-500" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 text-text-muted dark:text-gray-500" />
          )}
        </span>
      </button>

      {/* Steps list (collapsible) */}
      {expanded && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          transition={{ duration: 0.2 }}
          className="mt-2 space-y-1 overflow-hidden"
        >
          {(plan.steps ?? []).map((step, idx) => (
            <div
              key={step.id}
              className="flex items-center gap-2 text-xs"
            >
              <span className="text-text-muted dark:text-gray-600 w-4 text-right shrink-0">
                {idx === 0 ? '┌' : idx === (plan.steps?.length ?? 0) - 1 ? '└' : '├'}
              </span>
              <span className="text-text-muted dark:text-gray-500 w-4 shrink-0">
                {idx + 1}.
              </span>
              <span className="flex-1 text-text-secondary dark:text-gray-300 truncate">
                {step.description}
              </span>
              <span className={cn('shrink-0 font-medium', STATUS_STYLES[step.status])}>
                {STATUS_LABELS[step.status]}
              </span>
            </div>
          ))}
        </motion.div>
      )}

      {/* Footer badges */}
      <div className="mt-2 flex items-center gap-3 text-xs text-text-muted dark:text-gray-500">
        {plan.estimatedDuration != null && (
          <span>Estimated: ~{Math.round(plan.estimatedDuration)}s</span>
        )}
        {plan.complexity && (
          <Badge variant="default" size="sm">
            {plan.complexity}
          </Badge>
        )}
      </div>
    </motion.div>
  )
}
