// ============================================================
// Nexus - System Warning Banner
// Non-intrusive banner for system warnings, budget alerts, resource alerts
// Auto-dismisses after 10s or on user action
// ============================================================

import { useState, useEffect, useCallback } from 'react'
import { AlertTriangle, Clock, Cpu, X, RefreshCw } from 'lucide-react'
import { cn } from '../../lib/cn'

export interface SystemWarning {
  id: string
  type: 'inactivity' | 'long_running' | 'budget' | 'resource' | 'crash'
  level: 'info' | 'warning' | 'critical'
  message: string
  timestamp: number
  taskId?: string
  sessionId?: string
  /** If true, show retry button */
  retryable?: boolean
}

interface SystemWarningBannerProps {
  warnings: SystemWarning[]
  onDismiss: (id: string) => void
  onRetry?: (taskId: string) => void
  className?: string
}

const ICON_MAP = {
  inactivity: Clock,
  long_running: Clock,
  budget: AlertTriangle,
  resource: Cpu,
  crash: AlertTriangle,
}

const LEVEL_STYLES = {
  info: 'bg-blue-500/10 border-blue-500/30 text-blue-200',
  warning: 'bg-yellow-500/10 border-yellow-500/30 text-yellow-200',
  critical: 'bg-red-500/10 border-red-500/30 text-red-200',
}

const AUTO_DISMISS_MS = 10000 // 10 seconds

export function SystemWarningBanner({
  warnings,
  onDismiss,
  onRetry,
  className,
}: SystemWarningBannerProps) {
  // Auto-dismiss warnings after timeout
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = []
    for (const w of warnings) {
      if (w.level !== 'critical') { // Don't auto-dismiss critical
        const timer = setTimeout(() => onDismiss(w.id), AUTO_DISMISS_MS)
        timers.push(timer)
      }
    }
    return () => timers.forEach(clearTimeout)
  }, [warnings, onDismiss])

  if (warnings.length === 0) return null

  return (
    <div className={cn('space-y-1', className)}>
      {warnings.slice(0, 3).map((warning) => {
        const Icon = ICON_MAP[warning.type] || AlertTriangle
        const levelStyle = LEVEL_STYLES[warning.level] || LEVEL_STYLES.info

        return (
          <div
            key={warning.id}
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-md border text-xs',
              'animate-in fade-in slide-in-from-top-1 duration-300',
              levelStyle,
            )}
          >
            <Icon className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="flex-1 truncate">{warning.message}</span>
            {warning.retryable && onRetry && warning.taskId && (
              <button
                onClick={() => onRetry(warning.taskId!)}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs
                           hover:bg-white/10 transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
                Retry
              </button>
            )}
            <button
              onClick={() => onDismiss(warning.id)}
              className="p-0.5 rounded hover:bg-white/10 transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )
      })}
      {warnings.length > 3 && (
        <div className="text-xs text-muted-foreground pl-2">
          +{warnings.length - 3} more warnings
        </div>
      )}
    </div>
  )
}

// ── Hook for managing warnings state ──

export function useSystemWarnings() {
  const [warnings, setWarnings] = useState<SystemWarning[]>([])

  const addWarning = useCallback((warning: Omit<SystemWarning, 'id' | 'timestamp'>) => {
    const newWarning: SystemWarning = {
      ...warning,
      id: `warn-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: Date.now(),
    }
    setWarnings((prev) => {
      // Dedup: don't add if same type + similar message exists within 5s
      const isDup = prev.some(
        (w) => w.type === warning.type && Date.now() - w.timestamp < 5000
      )
      if (isDup) return prev
      // Keep max 5 warnings
      const updated = [...prev, newWarning]
      return updated.slice(-5)
    })
  }, [])

  const dismissWarning = useCallback((id: string) => {
    setWarnings((prev) => prev.filter((w) => w.id !== id))
  }, [])

  const clearAll = useCallback(() => {
    setWarnings([])
  }, [])

  return { warnings, addWarning, dismissWarning, clearAll }
}
