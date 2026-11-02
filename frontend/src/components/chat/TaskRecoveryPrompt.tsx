// ============================================================
// Nexus - Task Recovery Prompt
// Shows notification when a task crashed, with retry button
// ============================================================

import { AlertTriangle, RefreshCw, X } from 'lucide-react'
import { cn } from '../../lib/cn'

interface CrashedTask {
  taskId: string
  sessionId: string
  title: string
  message: string
  timestamp: number
}

interface TaskRecoveryPromptProps {
  crashedTasks: CrashedTask[]
  onRetry: (task: CrashedTask) => void
  onDismiss: (taskId: string) => void
  className?: string
}

export function TaskRecoveryPrompt({
  crashedTasks,
  onRetry,
  onDismiss,
  className,
}: TaskRecoveryPromptProps) {
  if (crashedTasks.length === 0) return null

  return (
    <div className={cn('space-y-2', className)}>
      {crashedTasks.map((task) => (
        <div
          key={task.taskId}
          className="flex items-start gap-3 p-3 rounded-lg border
                     bg-red-500/5 border-red-500/20 animate-in fade-in duration-300"
        >
          <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-red-200">
              Task stopped unexpectedly
            </p>
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {task.title || 'Unknown task'}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {task.message}
            </p>
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={() => onRetry(task)}
                className="flex items-center gap-1.5 px-3 py-1 rounded-md text-xs
                           bg-blue-500/20 text-blue-300 hover:bg-blue-500/30
                           border border-blue-500/30 transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
                Retry
              </button>
              <button
                onClick={() => onDismiss(task.taskId)}
                className="flex items-center gap-1 px-2 py-1 rounded-md text-xs
                           text-muted-foreground hover:bg-white/5 transition-colors"
              >
                <X className="w-3 h-3" />
                Dismiss
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export type { CrashedTask }
