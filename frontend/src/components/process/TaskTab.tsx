// ============================================================
// MekongAI Nexus - TaskTab
// Compact tab component for MultiTaskDashboard
// Shows: status dot, title, progress bar, agent count, duration
// ============================================================

import { useMemo } from 'react'
import { Users, Clock } from 'lucide-react'
import { cn } from '../../lib/cn'
import { ProgressBar } from '../ui/ProgressBar'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TaskTabData {
  id: string
  title: string
  status: string
  progress: number
  agentCount: number
  duration: number
  stage?: string
}

export interface TaskTabProps {
  task: TaskTabData
  active: boolean
  onClick: () => void
  className?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_DOT_COLORS: Record<string, string> = {
  pending: 'bg-gray-400',
  queued: 'bg-gray-400',
  planning: 'bg-amber-400',
  decomposing: 'bg-amber-400',
  executing: 'bg-blue-500',
  running: 'bg-blue-500',
  completed: 'bg-emerald-500',
  done: 'bg-emerald-500',
  failed: 'bg-red-500',
  cancelled: 'bg-red-500',
}

function getStatusDotColor(status: string): string {
  return STATUS_DOT_COLORS[status] ?? 'bg-gray-400'
}

function isActiveStatus(status: string): boolean {
  return ['executing', 'running', 'planning', 'decomposing'].includes(status)
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s.toString().padStart(2, '0')}s`
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TaskTab({ task, active, onClick, className }: TaskTabProps) {
  const dotColor = useMemo(() => getStatusDotColor(task.status), [task.status])
  const isRunning = useMemo(() => isActiveStatus(task.status), [task.status])
  const truncatedTitle = useMemo(
    () => task.title.length > 28 ? task.title.slice(0, 26) + '…' : task.title,
    [task.title],
  )

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group flex items-center gap-2.5 px-3 py-2 rounded-lg shrink-0',
        'border transition-all duration-150 min-w-[200px] max-w-[320px]',
        'text-left',
        active
          ? [
              'border-brand-500/50 bg-brand-50/50 shadow-sm',
              'dark:border-brand-500/30 dark:bg-brand-900/20',
            ]
          : [
              'border-gray-200 bg-white hover:border-gray-300 hover:shadow-subtle',
              'dark:border-[#2a2a45] dark:bg-[#1a1a2e] dark:hover:border-[#3a3a55]',
            ],
        className,
      )}
      aria-pressed={active}
      title={task.title}
    >
      {/* Status dot */}
      <span className="relative flex h-2.5 w-2.5 shrink-0">
        {isRunning && (
          <span
            className={cn(
              'absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping',
              dotColor,
            )}
          />
        )}
        <span className={cn('relative inline-flex h-2.5 w-2.5 rounded-full', dotColor)} />
      </span>

      {/* Content */}
      <div className="flex-1 min-w-0 space-y-1.5">
        {/* Title */}
        <span
          className={cn(
            'block text-xs font-medium leading-tight truncate',
            active
              ? 'text-brand-700 dark:text-brand-300'
              : 'text-gray-700 dark:text-gray-300',
          )}
        >
          {truncatedTitle}
        </span>

        {/* Progress bar */}
        <ProgressBar
          value={task.progress}
          size="sm"
          variant={
            task.status === 'completed' || task.status === 'done'
              ? 'success'
              : task.status === 'failed'
                ? 'error'
                : 'default'
          }
          animated={isRunning}
        />

        {/* Stats row */}
        <div className="flex items-center gap-3 text-[10px] text-gray-500 dark:text-gray-400">
          {/* Progress percentage */}
          <span className="font-medium tabular-nums">
            {Math.round(task.progress)}%
          </span>

          {/* Agent count */}
          {task.agentCount > 0 && (
            <span className="flex items-center gap-0.5">
              <Users className="h-2.5 w-2.5" aria-hidden />
              {task.agentCount}
            </span>
          )}

          {/* Duration */}
          <span className="flex items-center gap-0.5 tabular-nums">
            <Clock className="h-2.5 w-2.5" aria-hidden />
            {formatDuration(task.duration)}
          </span>

          {/* Stage label */}
          {task.stage && (
            <span className="capitalize opacity-70">
              {task.stage}
            </span>
          )}
        </div>
      </div>
    </button>
  )
}
