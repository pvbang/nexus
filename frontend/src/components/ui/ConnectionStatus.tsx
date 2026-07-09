// ============================================================
// MekongAI Nexus - ConnectionStatus UI Component
// Visual indicator for WebSocket connection status
// Supports dark mode, compact mode, and tooltip
// ============================================================

import { useRealtimeStore } from '../../stores/realtimeStore'
import { cn } from '../../lib/cn'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ConnectionStatusProps {
  /** Additional CSS classes */
  className?: string
  /** Show text label beside the dot (default: true) */
  showLabel?: boolean
  /** Compact mode — only show the dot, no label (default: false) */
  compact?: boolean
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a Unix timestamp (seconds or ms) to a human-readable time string */
function formatHeartbeatTime(timestamp: number): string {
  // Handle both seconds and milliseconds
  const ms = timestamp > 1e12 ? timestamp : timestamp * 1000
  const date = new Date(ms)
  return date.toLocaleTimeString()
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ConnectionStatus({
  className,
  showLabel = true,
  compact = false,
}: ConnectionStatusProps) {
  const connected = useRealtimeStore((s) => s.connected)
  const reconnecting = useRealtimeStore((s) => s.reconnecting)
  const lastHeartbeat = useRealtimeStore((s) => s.lastHeartbeat)

  // Determine visual state
  const status = connected ? 'connected' : reconnecting ? 'reconnecting' : 'disconnected'

  const dotColor: Record<typeof status, string> = {
    connected: 'bg-emerald-500',
    reconnecting: 'bg-amber-500',
    disconnected: 'bg-red-500',
  }

  const pulseColor: Record<typeof status, string> = {
    connected: 'bg-emerald-400',
    reconnecting: 'bg-amber-400',
    disconnected: 'bg-red-400',
  }

  const labelText: Record<typeof status, string> = {
    connected: 'Live',
    reconnecting: 'Reconnecting…',
    disconnected: 'Offline',
  }

  const labelColor: Record<typeof status, string> = {
    connected: 'text-emerald-600 dark:text-emerald-400',
    reconnecting: 'text-amber-600 dark:text-amber-400',
    disconnected: 'text-gray-500 dark:text-gray-400',
  }

  // Tooltip content
  const tooltipText = lastHeartbeat
    ? `Last heartbeat: ${formatHeartbeatTime(lastHeartbeat)}`
    : status === 'connected'
      ? 'Connected — awaiting first heartbeat'
      : status === 'reconnecting'
        ? 'Attempting to reconnect…'
        : 'No connection to server'

  const shouldShowLabel = showLabel && !compact

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5',
        className,
      )}
      title={tooltipText}
      role="status"
      aria-label={`Connection status: ${labelText[status]}`}
    >
      {/* Dot indicator */}
      <span className="relative inline-flex h-2.5 w-2.5 shrink-0">
        {/* Pulse ring — only for connected and reconnecting */}
        {status !== 'disconnected' && (
          <span
            className={cn(
              'absolute inset-0 inline-flex h-full w-full rounded-full opacity-75',
              status === 'connected' && 'animate-ping',
              status === 'reconnecting' && 'animate-pulse',
              pulseColor[status],
            )}
          />
        )}
        {/* Solid dot */}
        <span
          className={cn(
            'relative inline-flex h-full w-full rounded-full',
            dotColor[status],
          )}
        />
      </span>

      {/* Label text */}
      {shouldShowLabel && (
        <span
          className={cn(
            'text-xs font-medium leading-none select-none',
            labelColor[status],
          )}
        >
          {labelText[status]}
        </span>
      )}
    </div>
  )
}
