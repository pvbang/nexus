// ============================================================
// Nexus - TaskEventsReplay Component
// Task events replay panel showing execution events timeline
// ============================================================

import { useMemo } from 'react'
import { cn } from '../../lib/cn'
import { apiGet } from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { useRealtimeStore } from '../../stores/realtimeStore'
import { Timeline, type TimelineItem } from '../ui/Timeline'
import { Skeleton } from '../ui/Skeleton'
import { EmptyState } from '../ui/EmptyState'
import { POLLING_INTERVALS } from '../../lib/constants'
import type {
  ExecutionEvent,
  ToolEvent,
  SwarmRoundEvent,
} from '../../types/execution'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TaskEventsReplayProps {
  taskId: string
  className?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncateContent(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen) + '…'
}

function formatTimestamp(ts: number): string {
  const date = new Date(ts)
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function buildToolDoneDesc(te: ToolEvent): string {
  const parts: string[] = []
  if (te.output_preview) {
    parts.push(truncateContent(te.output_preview, 80))
  }
  if (te.execution_time_ms !== undefined) {
    parts.push(`${te.execution_time_ms}ms`)
  }
  return parts.join(' · ') || 'Completed'
}

function buildSwarmRoundDesc(se: SwarmRoundEvent): string {
  const parts: string[] = []
  if (se.proposals?.length) {
    parts.push(`${se.proposals.length} proposals`)
  }
  if (se.rebuttals?.length) {
    parts.push(`${se.rebuttals.length} rebuttals`)
  }
  if (se.verdict?.verdict) {
    parts.push(`Verdict: ${truncateContent(se.verdict.verdict, 60)}`)
  }
  return parts.join(' · ') || 'In progress'
}

const TOOL_ICON = (
  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.384-3.19A2.625 2.625 0 013 9.672V7.5a2.25 2.25 0 012.25-2.25h.862c.468 0 .922.146 1.3.416l3.788 2.71M14.58 15.17l5.384-3.19A2.625 2.625 0 0021 9.672V7.5a2.25 2.25 0 00-2.25-2.25h-.862a2.25 2.25 0 00-1.3.416l-3.788 2.71" />
  </svg>
)

const THINKING_ICON = (
  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
  </svg>
)

function eventToTimelineItem(event: ExecutionEvent): TimelineItem {
  switch (event.type) {
    case 'step':
      return {
        id: event.id,
        icon: <span className="text-sm">{event.icon || '📌'}</span>,
        title: event.title,
        description: event.description,
        status: event.status,
        timestamp: formatTimestamp(event.timestamp),
      }

    case 'tool_start':
      return {
        id: event.id,
        icon: TOOL_ICON,
        title: `Tool: ${event.tool}`,
        description: event.params_preview ? `Params: ${event.params_preview}` : undefined,
        status: 'active',
        timestamp: formatTimestamp(event.timestamp),
      }

    case 'tool_done':
      return {
        id: event.id,
        icon: TOOL_ICON,
        title: `Tool done: ${event.tool}`,
        description: buildToolDoneDesc(event),
        status: event.success === false ? 'error' : 'done',
        timestamp: formatTimestamp(event.timestamp),
      }

    case 'thinking':
      return {
        id: event.id,
        icon: THINKING_ICON,
        title: `${event.agent_name} thinking`,
        description: truncateContent(event.content, 120),
        status: 'active',
        timestamp: formatTimestamp(event.timestamp),
      }

    case 'agent_spawn':
      return {
        id: event.id,
        icon: <span className="text-sm">🤖</span>,
        title: `Agent spawned: ${event.agent_name}`,
        description: `Role: ${event.agent_role}`,
        status: 'done',
        timestamp: formatTimestamp(event.timestamp),
      }

    case 'swarm_round':
      return {
        id: event.id,
        icon: <span className="text-sm">🔄</span>,
        title: `Swarm Round ${event.round}/${event.total_rounds}`,
        description: buildSwarmRoundDesc(event),
        status: event.verdict ? 'done' : 'active',
        timestamp: formatTimestamp(event.timestamp),
      }

    default: {
      // Handle new event types (deep_analysis, self_reflection, etc.)
      const evt = event as ExecutionEvent & { data?: Record<string, unknown>; phase?: string }
      return {
        id: evt.id,
        icon: <span className="text-sm">📋</span>,
        title: evt.type.replace(/_/g, ' '),
        description: evt.phase || '',
        status: 'done' as const,
        timestamp: formatTimestamp(evt.timestamp),
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function EventsSkeleton() {
  return (
    <div className="space-y-4 py-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex gap-3">
          <Skeleton variant="circular" className="h-8 w-8 shrink-0" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// TaskEventsReplay
// ---------------------------------------------------------------------------

export function TaskEventsReplay({ taskId, className }: TaskEventsReplayProps) {
  const liveStatus = useRealtimeStore((s) => s.taskStatuses[taskId])
  const isActive = liveStatus
    ? ['executing', 'running', 'planning', 'decomposing'].includes(liveStatus.status)
    : false

  const { data: events, loading, error } = usePolling<ExecutionEvent[]>({
    fetcher: () => apiGet<ExecutionEvent[]>(`/tasks/${taskId}/events`),
    interval: POLLING_INTERVALS.normal,
    enabled: true,
  })

  const timelineItems: TimelineItem[] = useMemo(() => {
    if (!events || events.length === 0) return []
    return events.map(eventToTimelineItem)
  }, [events])

  // --- Loading state ---
  if (loading && !events) {
    return (
      <div className={className}>
        <EventsSkeleton />
      </div>
    )
  }

  // --- Error state ---
  if (error && !events) {
    return (
      <EmptyState
        icon="⚠️"
        title="Failed to load events"
        description={error}
        action={{
          label: 'Retry',
          onClick: () => window.location.reload(),
        }}
        className={className}
      />
    )
  }

  // --- Empty state ---
  if (!events || events.length === 0) {
    return (
      <EmptyState
        icon="📭"
        title="No events yet"
        description={
          isActive
            ? 'Events will appear here as the task executes.'
            : 'No execution events were recorded for this task.'
        }
        className={className}
      />
    )
  }

  // --- Timeline ---
  return (
    <div className={cn('space-y-3', className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
          {timelineItems.length} event{timelineItems.length !== 1 ? 's' : ''}
        </span>
        {isActive && (
          <span className="inline-flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-500 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-500" />
            </span>
            Live
          </span>
        )}
      </div>

      <Timeline items={timelineItems} />
    </div>
  )
}
