// ============================================================
// MekongAI Nexus - RecentActivity Dashboard Component
// Real-time event feed with timeline-style layout, type badges,
// relative timestamps, and auto-scroll to latest event
// ============================================================

import { useEffect, useRef } from 'react'
import { cn } from '../../lib/cn'
import { useRealtimeStore, type LiveEvent } from '../../stores/realtimeStore'
import { Card } from '../ui/Card'
import { Badge, type BadgeProps } from '../ui/Badge'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RecentActivityProps {
  /** Additional CSS classes */
  className?: string
  /** Maximum number of events to display (default: 20) */
  maxItems?: number
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map event types to badge variant and display label */
const eventTypeConfig: Record<string, { variant: NonNullable<BadgeProps['variant']>; label: string }> = {
  agent_status: { variant: 'purple', label: 'Agent' },
  task_update: { variant: 'info', label: 'Task' },
  swarm_event: { variant: 'brand', label: 'Swarm' },
  system: { variant: 'default', label: 'System' },
  metrics: { variant: 'success', label: 'Metrics' },
  error: { variant: 'error', label: 'Error' },
}

function getEventConfig(type: string): { variant: NonNullable<BadgeProps['variant']>; label: string } {
  return eventTypeConfig[type] || { variant: 'default', label: type }
}

/** Format a Unix timestamp to relative time */
function formatEventTime(ts: number): string {
  if (!ts) return ''
  const ms = ts > 1e12 ? ts : ts * 1000
  const diff = Date.now() - ms
  if (diff < 1000) return 'just now'
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return `${Math.floor(diff / 86400000)}d ago`
}

// ---------------------------------------------------------------------------
// Timeline dot colors matching event type
// ---------------------------------------------------------------------------

const dotColors: Record<string, string> = {
  agent_status: 'bg-purple-500',
  task_update: 'bg-blue-500',
  swarm_event: 'bg-violet-500',
  system: 'bg-gray-400 dark:bg-gray-500',
  metrics: 'bg-emerald-500',
  error: 'bg-red-500',
}

function getDotColor(type: string): string {
  return dotColors[type] || 'bg-gray-400 dark:bg-gray-500'
}

// ---------------------------------------------------------------------------
// EventItem sub-component
// ---------------------------------------------------------------------------

function EventItem({ event }: { event: LiveEvent }) {
  const config = getEventConfig(event.type)

  return (
    <div className="flex gap-3 group">
      {/* Timeline dot and line */}
      <div className="flex flex-col items-center shrink-0">
        <div
          className={cn(
            'w-2.5 h-2.5 rounded-full mt-1.5 ring-2 ring-white dark:ring-[#1a1a2e]',
            getDotColor(event.type),
          )}
        />
        <div className="w-px flex-1 bg-gray-200 dark:bg-[#2a2a45] group-last:bg-transparent" />
      </div>

      {/* Content */}
      <div className="flex-1 pb-4 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <Badge variant={config.variant} size="sm">
            {config.label}
          </Badge>
          <span className="text-[11px] text-text-muted dark:text-gray-500 tabular-nums">
            {formatEventTime(event.timestamp)}
          </span>
        </div>
        <p className="text-sm text-text-primary dark:text-gray-300 leading-relaxed truncate">
          {event.message}
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RecentActivity({ className, maxItems = 20 }: RecentActivityProps) {
  const recentEvents = useRealtimeStore((s) => s.recentEvents)
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevCountRef = useRef(0)

  // Display limited events
  const displayEvents = recentEvents.slice(0, maxItems)

  // Auto-scroll to top when new events arrive
  useEffect(() => {
    if (recentEvents.length > prevCountRef.current && scrollRef.current) {
      scrollRef.current.scrollTo({ top: 0, behavior: 'smooth' })
    }
    prevCountRef.current = recentEvents.length
  }, [recentEvents.length])

  return (
    <Card variant="default" padding="md" className={className}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100">
          🔔 Recent Activity
        </h3>
        {displayEvents.length > 0 && (
          <span className="text-xs text-text-muted dark:text-gray-500">
            {displayEvents.length} event{displayEvents.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Events list */}
      {displayEvents.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <span className="text-3xl mb-2" role="img" aria-hidden="true">📭</span>
          <p className="text-sm text-text-muted dark:text-gray-400">
            No events yet
          </p>
          <p className="text-xs text-text-muted dark:text-gray-500 mt-1">
            Events will appear here in real-time
          </p>
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="max-h-[360px] overflow-y-auto pr-1 scrollbar-thin"
        >
          {displayEvents.map((event) => (
            <EventItem key={event.id} event={event} />
          ))}
        </div>
      )}
    </Card>
  )
}
