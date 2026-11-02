// ============================================================
// Nexus - PlanView
// Displays task decomposition plan with timeline-style steps
// ============================================================

import {
  ListChecks,
  CircleDashed,
  Loader2,
  CheckCircle2,
  XCircle,
  User,
  Clock,
  Gauge,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/EmptyState'
import type { ProcessPlanStep } from '../../types/process'

// ── Step status icon mapping ──────────────────────────────────

const stepStatusConfig: Record<
  ProcessPlanStep['status'],
  { icon: typeof CircleDashed; color: string; label: string }
> = {
  pending: {
    icon: CircleDashed,
    color: 'text-gray-400 dark:text-gray-600',
    label: 'Pending',
  },
  running: {
    icon: Loader2,
    color: 'text-brand-500 dark:text-brand-400',
    label: 'Running',
  },
  completed: {
    icon: CheckCircle2,
    color: 'text-emerald-500 dark:text-emerald-400',
    label: 'Completed',
  },
  failed: {
    icon: XCircle,
    color: 'text-red-500 dark:text-red-400',
    label: 'Failed',
  },
}

// ── Complexity → badge variant mapping ────────────────────────

function complexityVariant(
  complexity: string | undefined,
): 'default' | 'success' | 'warning' | 'error' {
  if (!complexity) return 'default'
  const lower = complexity.toLowerCase()
  if (lower === 'simple' || lower === 'low') return 'success'
  if (lower === 'medium' || lower === 'moderate') return 'warning'
  return 'error'
}

// ── Main component ────────────────────────────────────────────

export function PlanView() {
  const plan = useProcessStore((s) => s.plan)

  if (!plan) {
    return (
      <EmptyState
        icon={<ListChecks className="h-10 w-10" />}
        title="No plan yet"
        description="The orchestrator will create a plan once processing begins."
      />
    )
  }

  return (
    <div className="space-y-4 animate-fade-in">
      {/* ── Header ────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-text-primary dark:text-gray-100">
          {plan.strategy || 'Task Plan'}
        </h3>
        {plan.complexity && (
          <Badge variant={complexityVariant(plan.complexity)} size="sm">
            <Gauge className="mr-1 h-3 w-3 inline" aria-hidden />
            {plan.complexity}
          </Badge>
        )}
        {plan.estimatedDuration != null && (
          <Badge variant="default" size="sm">
            <Clock className="mr-1 h-3 w-3 inline" aria-hidden />
            ~{Math.round(plan.estimatedDuration / 1000)}s
          </Badge>
        )}
      </div>

      {/* ── Steps timeline ────────────────────────────── */}
      <ol className="relative space-y-0" role="list" aria-label="Plan steps">
        {(plan.steps ?? []).map((step, idx) => {
          const cfg = stepStatusConfig[step.status]
          const StepIcon = cfg.icon
          const isLast = idx === (plan.steps?.length ?? 0) - 1

          return (
            <li key={step.id} className="relative flex gap-3 pb-5 last:pb-0">
              {/* Vertical line */}
              {!isLast && (
                <span
                  className={cn(
                    'absolute left-[15px] top-8 bottom-0 w-0.5',
                    step.status === 'completed'
                      ? 'bg-emerald-300 dark:bg-emerald-700'
                      : 'bg-border dark:bg-[#2a2a45]',
                  )}
                  aria-hidden
                />
              )}

              {/* Icon */}
              <span
                className={cn(
                  'relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
                  'bg-white ring-2 ring-border dark:bg-[#1a1a2e] dark:ring-[#2a2a45]',
                )}
              >
                <StepIcon
                  className={cn(
                    'h-4 w-4',
                    cfg.color,
                    step.status === 'running' && 'animate-spin',
                  )}
                  aria-label={cfg.label}
                />
              </span>

              {/* Content */}
              <div className="min-w-0 flex-1 pt-0.5">
                <div className="flex items-start justify-between gap-2">
                  <p
                    className={cn(
                      'text-sm font-medium leading-snug',
                      step.status === 'pending'
                        ? 'text-text-muted dark:text-gray-500'
                        : 'text-text-primary dark:text-gray-100',
                    )}
                  >
                    <span className="mr-1.5 text-xs text-text-muted dark:text-gray-500">
                      #{idx + 1}
                    </span>
                    {step.description}
                  </p>
                </div>

                {/* Agent badge */}
                {step.agentId && (
                  <span className="mt-1 inline-flex items-center gap-1 text-xs text-text-secondary dark:text-gray-400">
                    <User className="h-3 w-3" aria-hidden />
                    {step.agentId}
                  </span>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
