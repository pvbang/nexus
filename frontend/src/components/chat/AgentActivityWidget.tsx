// ============================================================
// MekongAI Nexus - Agent Activity Widget
// Shows active agents and their current status during task execution
// Collapsible sidebar/panel component
// ============================================================

import { useState, useMemo } from 'react'
import { ChevronDown, ChevronRight, Bot, Wrench, Brain, CheckCircle2 } from 'lucide-react'
import { cn } from '../../lib/cn'
import type { ExecutionEvent } from '../../types/execution'

interface AgentStatus {
  name: string
  role: string
  status: 'thinking' | 'tool_calling' | 'waiting' | 'done'
  currentTool?: string
  lastActivity?: string
}

interface AgentActivityWidgetProps {
  events: ExecutionEvent[]
  isRunning: boolean
  className?: string
}

const STATUS_ICONS = {
  thinking: Brain,
  tool_calling: Wrench,
  waiting: Bot,
  done: CheckCircle2,
}

const STATUS_COLORS = {
  thinking: 'text-purple-400',
  tool_calling: 'text-blue-400',
  waiting: 'text-muted-foreground',
  done: 'text-green-400',
}

const STATUS_LABELS = {
  thinking: 'Thinking...',
  tool_calling: 'Using tool',
  waiting: 'Waiting',
  done: 'Finished',
}

export function AgentActivityWidget({ events, isRunning, className }: AgentActivityWidgetProps) {
  const [expanded, setExpanded] = useState(true)

  // Derive agent statuses from events
  const agents = useMemo<AgentStatus[]>(() => {
    const agentMap = new Map<string, AgentStatus>()

    for (const evt of events) {
      const evtAny = evt as unknown as Record<string, unknown>
      const agentName = (evtAny.agent_name as string) || ''
      const agentRole = (evtAny.agent_role as string) || ''

      if (!agentName) continue

      // Use string comparison for event types that may not be in the strict union
      const evtType = String(evt.type)

      if (evtType === 'agent_spawn') {
        agentMap.set(agentName, {
          name: agentName,
          role: agentRole || 'agent',
          status: 'waiting',
        })
      } else if (evtType === 'thinking' || evtType === 'agent_thinking') {
        const existing = agentMap.get(agentName)
        if (existing) {
          existing.status = 'thinking'
          existing.lastActivity = (evtAny.content as string)?.slice(0, 80)
        }
      } else if (evtType === 'tool_start') {
        const existing = agentMap.get(agentName)
        if (existing) {
          existing.status = 'tool_calling'
          existing.currentTool = (evtAny.tool as string) || 'unknown'
        }
      } else if (evtType === 'tool_done') {
        const existing = agentMap.get(agentName)
        if (existing && existing.status === 'tool_calling') {
          existing.status = 'thinking'
          existing.currentTool = undefined
        }
      }
    }

    // If not running, mark all as done
    if (!isRunning) {
      for (const agent of agentMap.values()) {
        agent.status = 'done'
      }
    }

    return Array.from(agentMap.values())
  }, [events, isRunning])

  if (agents.length === 0) return null

  return (
    <div className={cn('rounded-lg border border-white/10 bg-white/[0.02]', className)}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs font-medium
                   text-muted-foreground hover:text-foreground transition-colors"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <Bot className="w-3.5 h-3.5" />
        <span>Agents ({agents.length})</span>
        {isRunning && (
          <span className="ml-auto flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            <span className="text-green-400">Active</span>
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-2 space-y-1">
          {agents.map((agent) => {
            const Icon = STATUS_ICONS[agent.status]
            const colorClass = STATUS_COLORS[agent.status]
            return (
              <div
                key={agent.name}
                className="flex items-center gap-2 py-1 text-xs"
              >
                <Icon className={cn('w-3 h-3', colorClass)} />
                <span className="font-medium truncate max-w-[100px]">{agent.name}</span>
                <span className="text-muted-foreground">({agent.role})</span>
                <span className={cn('ml-auto text-xs truncate max-w-[120px]', colorClass)}>
                  {agent.status === 'tool_calling' && agent.currentTool
                    ? `→ ${agent.currentTool}`
                    : STATUS_LABELS[agent.status]}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
