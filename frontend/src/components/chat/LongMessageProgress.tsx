// ============================================================
// MekongAI Nexus - LongMessageProgress
// Progress indicator when backend processes long messages in chunks
// ============================================================

import { cn } from '../../lib/cn'
import { ProgressBar } from '../ui/ProgressBar'

// ── Types ─────────────────────────────────────────────────────

export type LongMessagePhase = 'summarizing' | 'reading' | 'combining' | 'finalizing'

export interface LongMessageProgressProps {
  /** Current processing phase */
  phase: LongMessagePhase
  /** Current chunk being processed (1-based) */
  current: number
  /** Total chunks to process */
  total: number
  /** Additional class names */
  className?: string
}

// ── Helpers ───────────────────────────────────────────────────

const PHASE_LABELS: Record<LongMessagePhase, string> = {
  summarizing: 'Đang tóm tắt',
  reading: 'Đang đọc văn bản dài',
  combining: 'Đang ghép kết quả',
  finalizing: 'Đang hoàn tất',
}

const PHASE_VARIANTS: Record<LongMessagePhase, 'default' | 'warning' | 'success'> = {
  summarizing: 'default',
  reading: 'default',
  combining: 'warning',
  finalizing: 'success',
}

// ── Component ─────────────────────────────────────────────────

export function LongMessageProgress({
  phase,
  current,
  total,
  className,
}: LongMessageProgressProps) {
  const percentage = total > 0 ? Math.round((current / total) * 100) : 0
  const label = PHASE_LABELS[phase]

  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 px-4 py-2.5 rounded-lg',
        'bg-blue-50/60 border border-blue-200/50',
        'dark:bg-blue-900/10 dark:border-blue-800/30',
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-blue-700 dark:text-blue-300">
          {label}... (phần {current}/{total})
        </span>
        <span className="text-[11px] text-blue-500 dark:text-blue-400">
          {percentage}%
        </span>
      </div>
      <ProgressBar
        value={percentage}
        variant={PHASE_VARIANTS[phase]}
        size="sm"
        animated
      />
    </div>
  )
}
