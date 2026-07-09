// ============================================================
// MekongAI Nexus - Event Summary Badge
// Shows compact summary of events: "42 events • 15 tools • 3 debates"
// ============================================================

import { useMemo } from 'react'
import { Wrench, Users, MessageSquare, Zap } from 'lucide-react'
import { cn } from '../../lib/cn'
import type { ExecutionEvent } from '../../types/execution'

interface EventSummaryBadgeProps {
  events: ExecutionEvent[]
  className?: string
}

interface EventSummary {
  total: number
  tools: number
  debates: number
  agents: number
  thinking: number
  steps: number
}

export function EventSummaryBadge({ events, className }: EventSummaryBadgeProps) {
  const summary = useMemo<EventSummary>(() => {
    let tools = 0
    let debates = 0
    let agents = 0
    let thinking = 0
    let steps = 0

    for (const evt of events) {
      switch (evt.type) {
        case 'tool_start':
          tools++
          break
        case 'debate_proposal':
        case 'debate_rebuttal':
        case 'debate_verdict':
        case 'debate_critique':
        case 'debate_revision':
          debates++
          break
        case 'agent_spawn':
          agents++
          break
        case 'thinking':
          thinking++
          break
        case 'step':
          steps++
          break
      }
    }

    return { total: events.length, tools, debates, agents, thinking, steps }
  }, [events])

  if (summary.total === 0) return null

  return (
    <div className={cn('flex items-center gap-2 text-xs text-muted-foreground', className)}>
      <span className="font-medium">{summary.total} events</span>
      {summary.tools > 0 && (
        <span className="flex items-center gap-0.5">
          <Wrench className="w-3 h-3" />
          {summary.tools}
        </span>
      )}
      {summary.debates > 0 && (
        <span className="flex items-center gap-0.5">
          <MessageSquare className="w-3 h-3" />
          {summary.debates}
        </span>
      )}
      {summary.agents > 0 && (
        <span className="flex items-center gap-0.5">
          <Users className="w-3 h-3" />
          {summary.agents}
        </span>
      )}
      {summary.steps > 0 && (
        <span className="flex items-center gap-0.5">
          <Zap className="w-3 h-3" />
          {summary.steps}
        </span>
      )}
    </div>
  )
}
