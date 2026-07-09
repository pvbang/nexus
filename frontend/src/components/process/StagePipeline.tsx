// ============================================================
// MekongAI Nexus - StagePipeline
// Horizontal stepper showing all 8 orchestration stages
// ============================================================

import {
  Lightbulb,
  Users,
  MessageSquare,
  Play,
  Swords,
  Layers,
  Brain,
  Send,
  Check,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import type { ProcessStage } from '../../types/process'

/** Ordered pipeline stages (excludes idle/completed/failed meta-states) */
const STAGES: { key: ProcessStage; label: string; icon: typeof Lightbulb }[] = [
  { key: 'planning', label: 'Planning', icon: Lightbulb },
  { key: 'spawning', label: 'Spawning', icon: Users },
  { key: 'communicating', label: 'Comms', icon: MessageSquare },
  { key: 'executing', label: 'Executing', icon: Play },
  { key: 'debating', label: 'Debating', icon: Swords },
  { key: 'synthesizing', label: 'Synthesis', icon: Layers },
  { key: 'learning', label: 'Learning', icon: Brain },
  { key: 'responding', label: 'Responding', icon: Send },
]

const stageIndex = (stage: ProcessStage): number => {
  const idx = STAGES.findIndex((s) => s.key === stage)
  if (stage === 'completed') return STAGES.length
  if (stage === 'failed') return -1
  return idx
}

export interface StagePipelineProps {
  currentStage: ProcessStage
  /** Compact mode — icons only (auto on mobile via CSS) */
  compact?: boolean
  className?: string
}

export function StagePipeline({
  currentStage,
  compact = false,
  className,
}: StagePipelineProps) {
  const activeIdx = stageIndex(currentStage)

  return (
    <div
      className={cn(
        'flex items-center gap-1 overflow-x-auto py-2 scrollbar-none',
        className,
      )}
      role="list"
      aria-label="Process stages"
    >
      {STAGES.map((stage, idx) => {
        const isCompleted = activeIdx > idx
        const isActive = activeIdx === idx
        const isPending = activeIdx < idx
        const Icon = stage.icon

        return (
          <div key={stage.key} className="flex items-center" role="listitem">
            {/* Stage pill */}
            <div
              className={cn(
                'flex items-center gap-1.5 rounded-full-pill px-3 py-1.5 text-xs font-medium transition-all duration-300',
                // Completed
                isCompleted && [
                  'bg-emerald-50 text-emerald-700',
                  'dark:bg-emerald-900/30 dark:text-emerald-400',
                ],
                // Active
                isActive && [
                  'bg-brand-50 text-brand-800 ring-2 ring-brand-500/30',
                  'dark:bg-brand-900/30 dark:text-brand-400 dark:ring-brand-400/30',
                ],
                // Pending
                isPending && [
                  'bg-gray-50 text-gray-400',
                  'dark:bg-[#1a1a2e] dark:text-gray-600',
                ],
              )}
            >
              {isCompleted ? (
                <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />
              ) : (
                <Icon
                  className={cn(
                    'h-3.5 w-3.5 shrink-0',
                    isActive && 'animate-pulse-dot',
                  )}
                  aria-hidden
                />
              )}
              {/* Label: hidden in compact mode or on small screens */}
              <span
                className={cn(
                  'whitespace-nowrap',
                  compact ? 'hidden' : 'hidden sm:inline',
                )}
              >
                {stage.label}
              </span>
            </div>

            {/* Connector line */}
            {idx < STAGES.length - 1 && (
              <div
                className={cn(
                  'mx-0.5 h-px w-3 sm:w-5 shrink-0 transition-colors duration-300',
                  isCompleted
                    ? 'bg-emerald-400 dark:bg-emerald-600'
                    : 'bg-gray-200 dark:bg-[#2a2a45]',
                )}
                aria-hidden
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
