// ============================================================
// Nexus - LiveMetrics Dashboard Component
// Real-time system metrics from WebSocket realtimeStore
// Displays active tasks, agents, queue, cache, uptime, last event
// ============================================================

import { useEffect, useRef } from 'react'
import { cn } from '../../lib/cn'
import { useRealtimeStore } from '../../stores/realtimeStore'
import { Card } from '../ui/Card'
import { ConnectionStatus } from '../ui/ConnectionStatus'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LiveMetricsProps {
  /** Additional CSS classes */
  className?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format seconds into a human-readable duration string */
function formatUptime(totalSeconds: number): string {
  if (totalSeconds <= 0) return '0s'

  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = Math.floor(totalSeconds % 60)

  const parts: string[] = []
  if (days > 0) parts.push(`${days}d`)
  if (hours > 0) parts.push(`${hours}h`)
  if (minutes > 0) parts.push(`${minutes}m`)
  if (seconds > 0 && days === 0) parts.push(`${seconds}s`)

  return parts.join(' ')
}

/** Format a Unix timestamp to relative time */
function formatTimestamp(ts: number): string {
  if (!ts) return 'N/A'
  const ms = ts > 1e12 ? ts : ts * 1000
  const diff = Date.now() - ms
  if (diff < 1000) return 'just now'
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  return `${Math.floor(diff / 3600000)}h ago`
}

// ---------------------------------------------------------------------------
// MetricCell sub-component
// ---------------------------------------------------------------------------

interface MetricCellProps {
  label: string
  value: string | number
  icon: string
  /** Optional progress bar (0-100) */
  progress?: number
  /** Progress bar color class */
  progressColor?: string
  /** Whether the value just changed (for pulse) */
  pulse?: boolean
}

function MetricCell({ label, value, icon, progress, progressColor, pulse }: MetricCellProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 p-3 rounded-lg',
        'bg-gray-50 dark:bg-[#1f1f38]',
        'transition-all duration-300',
        pulse && 'animate-pulse-once',
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-text-muted dark:text-gray-400 uppercase tracking-wider">
          {label}
        </span>
        <span className="text-sm" aria-hidden="true">{icon}</span>
      </div>
      <div className="text-xl font-bold font-display text-text-dark dark:text-gray-100 tabular-nums">
        {value}
      </div>
      {progress !== undefined && (
        <div className="w-full h-1.5 bg-gray-200 dark:bg-[#2a2a45] rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-500 ease-out',
              progressColor || 'bg-brand-500',
            )}
            style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
          />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function LiveMetrics({ className }: LiveMetricsProps) {
  const metrics = useRealtimeStore((s) => s.systemMetrics)
  const connected = useRealtimeStore((s) => s.connected)
  const recentEvents = useRealtimeStore((s) => s.recentEvents)
  const prevMetricsRef = useRef(metrics)

  // Track which values changed for pulse animation
  const changedFields = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!metrics || !prevMetricsRef.current) {
      prevMetricsRef.current = metrics
      return
    }
    const prev = prevMetricsRef.current
    const changed = new Set<string>()

    if (prev.active_tasks !== metrics.active_tasks) changed.add('tasks')
    if (prev.active_agents !== metrics.active_agents) changed.add('agents')
    if (prev.queue_size !== metrics.queue_size) changed.add('queue')
    if (prev.cache_hit_rate !== metrics.cache_hit_rate) changed.add('cache')
    if (prev.uptime_seconds !== metrics.uptime_seconds) changed.add('uptime')

    changedFields.current = changed
    prevMetricsRef.current = metrics

    // Clear pulse after animation
    const timer = setTimeout(() => {
      changedFields.current = new Set()
    }, 600)
    return () => clearTimeout(timer)
  }, [metrics])

  // No metrics yet
  if (!metrics) {
    return (
      <Card variant="default" padding="md" className={className}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100">
            📊 Live Metrics
          </h3>
          <ConnectionStatus compact={false} />
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-center">
          {connected ? (
            <>
              <div className="text-3xl mb-2" role="img" aria-hidden="true">📡</div>
              <p className="text-sm text-text-muted dark:text-gray-400">
                Waiting for metrics data...
              </p>
            </>
          ) : (
            <>
              <div className="text-3xl mb-2" role="img" aria-hidden="true">🔌</div>
              <p className="text-sm text-text-muted dark:text-gray-400">
                Waiting for connection...
              </p>
              <ConnectionStatus className="mt-3" />
            </>
          )}
        </div>
      </Card>
    )
  }

  const lastEvent = recentEvents.length > 0 ? recentEvents[0] : null

  return (
    <Card variant="default" padding="md" className={className}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100">
          📊 Live Metrics
        </h3>
        <ConnectionStatus />
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <MetricCell
          label="Active Tasks"
          value={metrics.active_tasks}
          icon="📋"
          progress={Math.min(100, metrics.active_tasks * 10)}
          progressColor="bg-blue-500"
          pulse={changedFields.current.has('tasks')}
        />
        <MetricCell
          label="Active Agents"
          value={metrics.active_agents}
          icon="🤖"
          progress={Math.min(100, metrics.active_agents * 10)}
          progressColor="bg-emerald-500"
          pulse={changedFields.current.has('agents')}
        />
        <MetricCell
          label="Queue Size"
          value={metrics.queue_size}
          icon="📥"
          pulse={changedFields.current.has('queue')}
        />
        <MetricCell
          label="Cache Hit Rate"
          value={`${Math.round(metrics.cache_hit_rate * 100)}%`}
          icon="⚡"
          progress={metrics.cache_hit_rate * 100}
          progressColor="bg-amber-500"
          pulse={changedFields.current.has('cache')}
        />
        <MetricCell
          label="Uptime"
          value={formatUptime(metrics.uptime_seconds)}
          icon="⏱️"
          pulse={changedFields.current.has('uptime')}
        />
        <MetricCell
          label="Last Event"
          value={lastEvent ? formatTimestamp(lastEvent.timestamp) : 'N/A'}
          icon="🔔"
        />
      </div>
    </Card>
  )
}
