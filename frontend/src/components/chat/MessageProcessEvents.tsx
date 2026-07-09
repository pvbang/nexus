// ============================================================
// MekongAI Nexus - MessageProcessEvents
// Renders execution events associated with a specific assistant message.
// Shows collapsed summary by default, expandable to see full timeline.
// Used in chat history to preserve event context per-message.
// ============================================================

import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Timer,
  Wrench,
  Users,
  Brain,
  Search,
  MessageSquare,
  Sparkles,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'

// ── Types ─────────────────────────────────────────────────────

interface EventSummary {
  toolCalls: number
  agentsSpawned: number
  thinkingEvents: number
  debateRounds: number
  totalEvents: number
  durationMs?: number
  tools: string[]
  agents: string[]
}

/** Raw event from message metadata — loosely typed since backend schema varies */
type ProcessEvent = Record<string, unknown>

interface MessageProcessEventsProps {
  /** Raw execution events from message metadata */
  events: ProcessEvent[]
  /** Whether this is the currently active (live) task */
  isLive?: boolean
  /** Initial collapsed state (default: true for history) */
  defaultCollapsed?: boolean
}

// ── Accessors (safe property access on Record<string, unknown>) ──

function evtType(evt: ProcessEvent): string {
  return String(evt.type || evt.event_type || '')
}
function evtStr(evt: ProcessEvent, key: string): string {
  return String(evt[key] || '')
}
function evtNum(evt: ProcessEvent, key: string): number {
  return Number(evt[key] || 0)
}

// ── Helpers ───────────────────────────────────────────────────

function summarizeEvents(events: ProcessEvent[]): EventSummary {
  const tools = new Set<string>()
  const agents = new Set<string>()
  let toolCalls = 0
  let thinkingEvents = 0
  let debateRounds = 0
  let totalDuration = 0

  for (const evt of events) {
    const t = evtType(evt)
    if (t === 'tool_start' || t === 'tool_done') {
      if (t === 'tool_start') toolCalls++
      const tool = evtStr(evt, 'tool')
      if (tool) tools.add(tool)
      const dur = evtNum(evt, 'execution_time_ms')
      if (dur) totalDuration += dur
    }
    if (t === 'agent_thinking' || t === 'thinking') {
      thinkingEvents++
    }
    if (t === 'agent_spawn') {
      const name = evtStr(evt, 'agent_name')
      if (name) agents.add(name)
    }
    if (t === 'swarm_round' || t === 'debate_proposal') {
      debateRounds = Math.max(debateRounds, evtNum(evt, 'round'))
    }
    const agentName = evtStr(evt, 'agent_name')
    if (agentName) agents.add(agentName)
  }

  return {
    toolCalls,
    agentsSpawned: agents.size,
    thinkingEvents,
    debateRounds,
    totalEvents: events.length,
    durationMs: totalDuration > 0 ? totalDuration : undefined,
    tools: Array.from(tools).slice(0, 5),
    agents: Array.from(agents).slice(0, 5),
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}


/** Event types to skip in display (internal state, not user-meaningful) */
const SKIP_DISPLAY_TYPES = new Set([
  'task_started', 'task_completed', 'task_failed', 'task_cancelled',
  'react_phase', 'heartbeat', 'pong', 'ping',
])

function getEventLabel(evt: ProcessEvent): string {
  const t = evtType(evt)
  const agent = evtStr(evt, 'agent_name') || 'Agent'
  const tool = evtStr(evt, 'tool') || 'tool'
  const content = evtStr(evt, 'content') || evtStr(evt, 'preview') || ''
  switch (t) {
    case 'tool_start': return `🔧 ${agent} → ${tool}`
    case 'tool_done': {
      const success = evt.success !== false
      const timeStr = evtNum(evt, 'execution_time_ms') ? ` (${formatDuration(evtNum(evt, 'execution_time_ms'))})` : ''
      return `${success ? '✅' : '❌'} ${tool}${timeStr}`
    }
    case 'agent_spawn': return `🤖 ${agent} (${evtStr(evt, 'agent_role') || 'agent'})`
    case 'agent_thinking':
    case 'thinking':
    case 'agent_response': {
      const action = evtStr(evt, 'action')
      if (action === 'discussion') return `💬 ${agent}: Discussion response`
      return `💭 ${agent}: ${content.slice(0, 80)}${content.length > 80 ? '...' : ''}`
    }
    case 'document_created': {
      const docLabels: Record<string, string> = { TASK_BRIEF: 'Task Brief', ROE: 'Rules of Engagement', TODO: 'TODO List', TODO_UPDATE: 'TODO Updated' }
      return `📄 ${docLabels[evtStr(evt, 'document')] || evtStr(evt, 'document')} created`
    }
    case 'evolution_insight': return `✨ Evolution Agent — Turn ${evtNum(evt, 'turn')}`
    case 'deep_analysis':
    case 'deep_analysis_complete': return `🔬 Deep Analysis`
    case 'self_reflection': return `🪞 Self-Reflection`
    case 'auto_verify': return `🔍 Auto-Verify: ${evtStr(evt, 'tool_name') || tool}`
    case 'completion_check': return `✅ Completion Check`
    case 'mid_analysis': return `📊 Mid-Analysis`
    case 'planning_start': return `📋 Planning started`
    case 'planning_done': return `📋 Plan ready`
    case 'evaluation_done': return `📊 Evaluation done`
    case 'next_turn_plan': return `🗺️ Next turn plan`
    case 'swarm_round':
    case 'swarm_round_start': return `🗣️ Debate Round ${evtNum(evt, 'round') || ''}`
    case 'debate_proposal': return `🗣️ ${agent}: đưa ra quan điểm`
    case 'debate_rebuttal': return `⚔️ ${agent}: phản biện`
    case 'debate_critique': return `📝 Critique: ${content.slice(0, 60)}`
    case 'debate_revision': return `✏️ Revision: ${agent}`
    case 'debate_verdict': return `🧑‍⚖️ Verdict`
    case 'xskill_retrieve': return `📚 XSkill: Retrieved knowledge`
    case 'xskill_accumulate': return `📚 XSkill: Learned experience`
    case 'step': return `${evtStr(evt, 'icon') || '⏳'} ${evtStr(evt, 'title') || `Step ${evtNum(evt, 'step')}`}`
    default: return t.replace(/_/g, ' ')
  }
}

// ── Rich Event Card ───────────────────────────────────────────

function EventCard({ evt }: { evt: ProcessEvent }) {
  const t = evtType(evt)
  const agent = evtStr(evt, 'agent_name') || ''
  const role = evtStr(evt, 'agent_role') || ''
  const tool = evtStr(evt, 'tool') || evtStr(evt, 'tool_name') || ''
  const content = evtStr(evt, 'content') || evtStr(evt, 'preview') || ''
  const execTime = evtNum(evt, 'execution_time_ms')
  const success = evt.success

  // Tool events — show tool name, agent, params, result, duration
  if (t === 'tool_start' || t === 'tool_done') {
    const isDone = t === 'tool_done' || success !== undefined
    return (
      <div className={cn(
        'rounded-md border px-3 py-2 text-xs',
        isDone
          ? success === false
            ? 'bg-red-50/50 border-red-100 dark:bg-red-900/10 dark:border-red-900/30'
            : 'bg-emerald-50/50 border-emerald-100 dark:bg-emerald-900/10 dark:border-emerald-900/30'
          : 'bg-blue-50/50 border-blue-100 dark:bg-blue-900/10 dark:border-blue-900/30',
      )}>
        <div className="flex items-center gap-2">
          <Wrench className="h-3.5 w-3.5 text-blue-500 shrink-0" />
          <span className="font-mono font-medium text-gray-800 dark:text-gray-200">{tool}</span>
          {agent && (
            <span className="text-gray-500 dark:text-gray-400">by {agent}</span>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            {execTime > 0 && (
              <span className="text-gray-400 dark:text-gray-500 tabular-nums flex items-center gap-0.5">
                <Timer className="h-2.5 w-2.5" />
                {formatDuration(execTime)}
              </span>
            )}
            {isDone && success !== false && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />}
            {success === false && <span className="text-red-500 font-bold">✗</span>}
          </span>
        </div>
        {content && (
          <div className="mt-1 text-[11px] text-gray-600 dark:text-gray-400 truncate max-w-full">
            {content.slice(0, 150)}{content.length > 150 ? '...' : ''}
          </div>
        )}
      </div>
    )
  }

  // Agent spawn
  if (t === 'agent_spawn') {
    return (
      <div className="rounded-md border border-purple-100 dark:border-purple-900/30 bg-purple-50/50 dark:bg-purple-900/10 px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <Users className="h-3.5 w-3.5 text-purple-500 shrink-0" />
          <span className="font-medium text-purple-700 dark:text-purple-300">{agent}</span>
          {role && <span className="text-purple-500/70 dark:text-purple-400/70">({role})</span>}
        </div>
      </div>
    )
  }

  // Thinking / agent_response
  if (t === 'agent_thinking' || t === 'thinking' || t === 'agent_response') {
    const action = evtStr(evt, 'action')
    const challenges = Array.isArray(evt.challenges) ? evt.challenges as string[] : []
    const supports = Array.isArray(evt.supports) ? evt.supports as string[] : []
    const targetAgents = Array.isArray(evt.target_agents) ? evt.target_agents as string[] : []

    // Structured discussion card
    if (action === 'discussion' && (challenges.length > 0 || supports.length > 0)) {
      return (
        <div className="rounded-md border border-amber-100 dark:border-amber-900/30 bg-amber-50/50 dark:bg-amber-950/20 p-3 space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium">
            <MessageSquare className="w-3.5 h-3.5 text-amber-600 shrink-0" />
            <span className="text-amber-700 dark:text-amber-300">{agent}</span>
            {role && <span className="text-[10px] text-gray-500 dark:text-gray-400">{role}</span>}
            {targetAgents.length > 0 && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500">→ {targetAgents.join(', ')}</span>
            )}
          </div>

          {/* Challenges */}
          {challenges.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] font-semibold text-orange-600 dark:text-orange-400">⚡ Challenges</div>
              {challenges.map((c, i) => (
                <div key={i} className="text-[11px] text-gray-700 dark:text-gray-300 pl-3 border-l-2 border-orange-300 dark:border-orange-700">{String(c)}</div>
              ))}
            </div>
          )}

          {/* Supports */}
          {supports.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] font-semibold text-green-600 dark:text-green-400">✅ Supports</div>
              {supports.map((s, i) => (
                <div key={i} className="text-[11px] text-gray-700 dark:text-gray-300 pl-3 border-l-2 border-green-300 dark:border-green-700">{String(s)}</div>
              ))}
            </div>
          )}
        </div>
      )
    }

    // Default thinking display (backward compat)
    return (
      <div className="rounded-md border border-amber-100 dark:border-amber-900/30 bg-amber-50/30 dark:bg-amber-900/10 px-3 py-2 text-xs">
        <div className="flex items-center gap-2 mb-0.5">
          <Brain className="h-3.5 w-3.5 text-amber-500 shrink-0" />
          <span className="font-medium text-amber-700 dark:text-amber-300">{agent}</span>
          {role && <span className="text-amber-500/60 text-[10px]">{role}</span>}
        </div>
        {content && (
          <div className="text-[11px] text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words leading-relaxed">
            {content.slice(0, 300)}{content.length > 300 ? '...' : ''}
          </div>
        )}
      </div>
    )
  }

  // Deep analysis
  if (t === 'deep_analysis' || t === 'deep_analysis_complete') {
    return (
      <div className="rounded-md border border-indigo-100 dark:border-indigo-900/30 bg-indigo-50/50 dark:bg-indigo-900/10 px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <Search className="h-3.5 w-3.5 text-indigo-500 shrink-0" />
          <span className="font-medium text-indigo-700 dark:text-indigo-300">Deep Analysis</span>
        </div>
        {content && (
          <div className="mt-1 text-[11px] text-gray-600 dark:text-gray-400 whitespace-pre-line">
            {content.slice(0, 300).replace(/(?<=\S)\s*(\d+\))/g, '\n$1')}
          </div>
        )}
      </div>
    )
  }

  // Step events
  if (t === 'step') {
    const icon = evtStr(evt, 'icon') || '⏳'
    const title = evtStr(evt, 'title') || `Step ${evtNum(evt, 'step')}`
    const description = evtStr(evt, 'description') || ''
    return (
      <div className="rounded-md border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/30 px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-sm">{icon}</span>
          <span className="font-medium text-gray-800 dark:text-gray-200">{title}</span>
        </div>
        {description && (
          <div className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">{description}</div>
        )}
      </div>
    )
  }

  // Planning start
  if (t === 'planning_start') {
    return (
      <div className="rounded-md border border-blue-100 dark:border-blue-900/30 bg-blue-50/30 dark:bg-blue-900/10 px-3 py-1.5 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-sm">📋</span>
          <span className="font-medium text-blue-700 dark:text-blue-300">Planning started</span>
        </div>
      </div>
    )
  }

  // Planning done — show orchestrator plan details
  if (t === 'planning_done') {
    const data = (evt.data || evt) as Record<string, unknown>
    const assignments = Array.isArray(data.assignments) ? data.assignments : []
    return (
      <div className="rounded-md border border-indigo-200 dark:border-indigo-500/30 bg-indigo-50/30 dark:bg-indigo-900/20 px-3 py-2 text-xs space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-sm">📋</span>
          <span className="font-medium text-indigo-700 dark:text-indigo-300">Orchestrator Plan</span>
          <span className="text-[11px] text-gray-500 dark:text-gray-400">
            {evtStr(data as ProcessEvent, 'complexity')} • {evtNum(data as ProcessEvent, 'turns_planned')} turns • {evtNum(data as ProcessEvent, 'agents_count')} agents
          </span>
        </div>
        {evtStr(data as ProcessEvent, 'intent') && (
          <p className="text-[11px] text-gray-600 dark:text-gray-300">{evtStr(data as ProcessEvent, 'intent')}</p>
        )}
        {assignments.length > 0 && (
          <div className="space-y-1 mt-1">
            <span className="text-[10px] text-gray-500 dark:text-gray-400 font-medium">Assignments:</span>
            {(assignments as Array<Record<string, string>>).map((a, i) => (
              <div key={i} className="pl-2 border-l-2 border-indigo-300 dark:border-indigo-500/50">
                <span className="font-medium text-indigo-700 dark:text-indigo-300">{a.agent || a.role}</span>
                {a.role && <span className="text-gray-500 dark:text-gray-400 ml-1">({a.role})</span>}
                {a.task && <p className="text-[11px] text-gray-600 dark:text-gray-300 mt-0.5">{a.task}</p>}
                {a.success_criteria && <p className="text-[11px] text-green-600 dark:text-green-400/70">✓ {a.success_criteria}</p>}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // Micro step — orchestrator turn progress
  if (t === 'micro_step') {
    const data = (evt.data || evt) as Record<string, unknown>
    const phaseIcons: Record<string, string> = {
      thinking: '🧠',
      discussion: '💬',
      execution: '⚡',
      planning: '📋',
    }
    const phase = String(data.phase || '')
    return (
      <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 py-0.5">
        <span>{phaseIcons[phase] || '•'}</span>
        <span>{String(data.description || t)}</span>
      </div>
    )
  }

  // Evaluation done — rich display with progress, decision, missing items
  if (t === 'evaluation_done') {
    const data = (evt.data || evt) as Record<string, unknown>
    const progressPct = Number(data.progress_pct || 0)
    const decision = String(data.decision || '')
    const isComplete = decision === 'complete'
    const missingItems = Array.isArray(data.missing_items) ? data.missing_items as string[] : []
    const evaluationText = String(data.evaluation_text || '')
    const nextTurnFocus = String(data.next_turn_focus || '')
    const agentEvaluations = Array.isArray(data.agent_evaluations) ? data.agent_evaluations as Array<Record<string, unknown>> : []
    const contradictions = Array.isArray(data.contradictions) ? data.contradictions as string[] : []

    return (
      <div className={cn(
        'rounded-md border px-3 py-2 text-xs space-y-2',
        isComplete
          ? 'border-green-200 dark:border-green-500/30 bg-green-50/30 dark:bg-green-900/10'
          : 'border-yellow-200 dark:border-yellow-500/30 bg-yellow-50/30 dark:bg-yellow-900/10',
      )}>
        {/* Header: icon + decision badge + progress */}
        <div className="flex items-center gap-2">
          <span className="text-sm">{isComplete ? '✅' : '📊'}</span>
          <span className={cn('font-medium', isComplete ? 'text-green-700 dark:text-green-300' : 'text-yellow-700 dark:text-yellow-300')}>
            Evaluation
          </span>
          {decision && (
            <span className={cn(
              'px-1.5 py-0.5 rounded text-[10px] font-medium',
              isComplete
                ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                : 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300',
            )}>
              {decision}
            </span>
          )}
        </div>

        {/* Progress bar */}
        {progressPct > 0 && (
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all',
                  progressPct >= 100 ? 'bg-green-500' : progressPct >= 60 ? 'bg-yellow-500' : 'bg-orange-500',
                )}
                style={{ width: `${Math.min(progressPct, 100)}%` }}
              />
            </div>
            <span className="text-[11px] text-gray-500 dark:text-gray-400 tabular-nums">{Math.round(progressPct)}%</span>
          </div>
        )}

        {/* Evaluation text */}
        {evaluationText && (
          <p className="text-[11px] text-gray-600 dark:text-gray-300 leading-relaxed">{evaluationText}</p>
        )}

        {/* Agent evaluations */}
        {agentEvaluations.length > 0 && (
          <div className="space-y-1">
            <span className="text-[10px] text-gray-500 dark:text-gray-400 font-medium">Agent Performance:</span>
            {agentEvaluations.map((ae, i) => (
              <div key={i} className="flex items-center gap-2 pl-2 border-l-2 border-gray-200 dark:border-gray-700">
                <span className="font-medium text-gray-700 dark:text-gray-300">{String(ae.agent_name || '')}</span>
                {ae.score != null && <span className="text-[10px] text-gray-500">{Math.round(Number(ae.score) * 100)}%</span>}
                {ae.summary ? <span className="text-[11px] text-gray-500 dark:text-gray-400 truncate">— {String(ae.summary)}</span> : null}
              </div>
            ))}
          </div>
        )}

        {/* Missing items */}
        {missingItems.length > 0 && (
          <div className="space-y-0.5">
            <span className="text-[10px] text-yellow-600 dark:text-yellow-400 font-medium">Missing:</span>
            <ul className="list-disc list-inside text-[11px] text-gray-600 dark:text-gray-400 space-y-0.5">
              {missingItems.map((item, i) => (
                <li key={i}>{String(item)}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Contradictions */}
        {contradictions.length > 0 && (
          <div className="space-y-0.5">
            <span className="text-[10px] text-red-600 dark:text-red-400 font-medium">Contradictions:</span>
            <ul className="list-disc list-inside text-[11px] text-gray-600 dark:text-gray-400 space-y-0.5">
              {contradictions.map((item, i) => (
                <li key={i}>{String(item)}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Next turn focus */}
        {nextTurnFocus && (
          <div className="text-[11px] text-blue-600 dark:text-blue-400">
            <span className="font-medium">Next focus:</span> {nextTurnFocus}
          </div>
        )}
      </div>
    )
  }

  // Next turn plan — show turn assignments/routing
  if (t === 'next_turn_plan') {
    const data = (evt.data || evt) as Record<string, unknown>
    const turn = evtNum(data as ProcessEvent, 'turn')
    const objective = String(data.objective || '')
    const milestone = String(data.milestone || '')
    const executionMode = String(data.execution_mode || '')
    const assignments = Array.isArray(data.assignments) ? data.assignments as Array<Record<string, string>> : []

    return (
      <div className="rounded-md border border-indigo-200 dark:border-indigo-500/30 bg-indigo-50/30 dark:bg-indigo-900/20 px-3 py-2 text-xs space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-sm">🗺️</span>
          <span className="font-medium text-indigo-700 dark:text-indigo-300">Turn {turn} Plan</span>
          {executionMode && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-300">
              {executionMode}
            </span>
          )}
        </div>
        {objective && (
          <p className="text-[11px] text-gray-600 dark:text-gray-300">{objective}</p>
        )}
        {milestone && (
          <p className="text-[11px] text-indigo-600 dark:text-indigo-400">
            <span className="font-medium">Milestone:</span> {milestone}
          </p>
        )}
        {assignments.length > 0 && (
          <div className="space-y-1 mt-1">
            <span className="text-[10px] text-gray-500 dark:text-gray-400 font-medium">Assignments:</span>
            {assignments.map((a, i) => (
              <div key={i} className="pl-2 border-l-2 border-indigo-300 dark:border-indigo-500/50">
                <span className="font-medium text-indigo-700 dark:text-indigo-300">{a.agent_name || 'Agent'}</span>
                {a.agent_role && <span className="text-gray-500 dark:text-gray-400 ml-1">({a.agent_role})</span>}
                {a.task && <p className="text-[11px] text-gray-600 dark:text-gray-300 mt-0.5">{a.task}</p>}
                {a.success_criteria && <p className="text-[11px] text-green-600 dark:text-green-400/70">✓ {a.success_criteria}</p>}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // Debate events
  if (t === 'swarm_round' || t === 'debate_proposal' || t === 'debate_rebuttal' || t === 'debate_verdict' || t === 'debate_critique' || t === 'debate_revision') {
    return (
      <div className="rounded-md border border-orange-100 dark:border-orange-900/30 bg-orange-50/30 dark:bg-orange-900/10 px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-3.5 w-3.5 text-orange-500 shrink-0" />
          <span className="font-medium text-orange-700 dark:text-orange-300">
            {getEventLabel(evt)}
          </span>
        </div>
        {content && (
          <div className="mt-1 text-[11px] text-gray-600 dark:text-gray-400 whitespace-pre-wrap break-words">
            {content.slice(0, 200)}{content.length > 200 ? '...' : ''}
          </div>
        )}
      </div>
    )
  }

  // XSkill
  if (t === 'xskill_retrieve' || t === 'xskill_accumulate') {
    return (
      <div className="rounded-md border border-amber-100 dark:border-amber-900/30 bg-amber-50/30 dark:bg-amber-900/10 px-3 py-1.5 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-sm">📚</span>
          <span className="font-medium text-amber-700 dark:text-amber-300">
            {t === 'xskill_retrieve' ? 'Retrieved knowledge' : 'Learned experience'}
          </span>
        </div>
      </div>
    )
  }

  // Completion check / self-reflection / auto-verify
  if (t === 'completion_check' || t === 'self_reflection' || t === 'auto_verify' || t === 'mid_analysis') {
    const labels: Record<string, string> = {
      completion_check: '✅ Completion Check',
      self_reflection: '🪞 Self-Reflection',
      auto_verify: '🔍 Auto-Verify',
      mid_analysis: '📊 Mid-Analysis',
    }
    return (
      <div className="rounded-md border border-emerald-100 dark:border-emerald-900/30 bg-emerald-50/30 dark:bg-emerald-900/10 px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          <span className="font-medium text-emerald-700 dark:text-emerald-300">{labels[t] || t}</span>
        </div>
        {content && (
          <div className="mt-1 text-[11px] text-gray-600 dark:text-gray-400">
            {content.slice(0, 200)}
          </div>
        )}
      </div>
    )
  }

  // Document created
  if (t === 'document_created') {
    const docIcons: Record<string, string> = { TASK_BRIEF: '📋', ROE: '📜', TODO: '✅', TODO_UPDATE: '🔄' }
    const docLabels: Record<string, string> = { TASK_BRIEF: 'Task Brief', ROE: 'Rules of Engagement', TODO: 'TODO List', TODO_UPDATE: 'TODO Updated' }
    const docType = evtStr(evt, 'document')
    const description = evtStr(evt, 'description')
    return (
      <div className="rounded-md border border-blue-100 dark:border-blue-900/30 bg-blue-50/50 dark:bg-blue-950/20 p-2 flex items-center gap-2 text-xs">
        <span>{docIcons[docType] || '📄'}</span>
        <span className="font-medium text-blue-700 dark:text-blue-300">{docLabels[docType] || docType}</span>
        {description && <span className="text-[11px] text-gray-500 dark:text-gray-400">{description}</span>}
      </div>
    )
  }

  // Evolution insight
  if (t === 'evolution_insight') {
    const turnNum = evtNum(evt, 'turn')
    const trajectory = evtStr(evt, 'quality_trajectory')
    const improvementSuggestions = Array.isArray(evt.improvement_suggestions) ? evt.improvement_suggestions as string[] : []
    const blindSpots = Array.isArray(evt.blind_spots) ? evt.blind_spots as string[] : []
    const challengeQuestions = Array.isArray(evt.challenge_questions) ? evt.challenge_questions as string[] : []
    return (
      <div className="rounded-md border border-purple-100 dark:border-purple-900/30 bg-purple-50/50 dark:bg-purple-950/20 p-3 space-y-2 text-xs">
        <div className="flex items-center gap-2 font-medium text-purple-700 dark:text-purple-300">
          <Sparkles className="w-3.5 h-3.5 shrink-0" />
          <span>Evolution Agent — Turn {turnNum}</span>
          {trajectory && (
            <span className={cn(
              'text-[10px] px-1.5 py-0.5 rounded',
              trajectory === 'improving' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' :
              trajectory === 'declining' ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300' :
              'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300',
            )}>{trajectory}</span>
          )}
        </div>

        {improvementSuggestions.length > 0 && (
          <div className="space-y-1">
            <div className="text-[11px] font-semibold text-purple-600 dark:text-purple-400">💡 Improvement Suggestions</div>
            {improvementSuggestions.map((s, i) => (
              <div key={i} className="text-[11px] text-gray-700 dark:text-gray-300 pl-3">• {String(s)}</div>
            ))}
          </div>
        )}

        {blindSpots.length > 0 && (
          <div className="space-y-1">
            <div className="text-[11px] font-semibold text-amber-600 dark:text-amber-400">🔍 Blind Spots</div>
            {blindSpots.map((b, i) => (
              <div key={i} className="text-[11px] text-gray-700 dark:text-gray-300 pl-3">• {String(b)}</div>
            ))}
          </div>
        )}

        {challengeQuestions.length > 0 && (
          <div className="space-y-1">
            <div className="text-[11px] font-semibold text-blue-600 dark:text-blue-400">❓ Challenge Questions</div>
            {challengeQuestions.map((q, i) => (
              <div key={i} className="text-[11px] text-gray-700 dark:text-gray-300 pl-3 italic">"{String(q)}"</div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // Generic fallback
  return (
    <div className="rounded-md border border-gray-100 dark:border-gray-800 bg-gray-50/30 dark:bg-gray-900/20 px-3 py-1.5 text-xs">
      <div className="flex items-center gap-2">
        <MessageSquare className="h-3 w-3 text-gray-400 shrink-0" />
        <span className="text-gray-600 dark:text-gray-400">{getEventLabel(evt)}</span>
      </div>
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────

export function MessageProcessEvents({
  events,
  isLive = false,
  defaultCollapsed = true,
}: MessageProcessEventsProps) {
  const [isExpanded, setIsExpanded] = useState(!defaultCollapsed || isLive)

  const summary = useMemo(() => summarizeEvents(events), [events])

  // Filter out noise/internal events for display
  const displayEvents = useMemo(() => {
    const NOISE = new Set(['token', 'done', 'error', 'usage', 'heartbeat', 'pong', 'ping'])
    return events.filter((e) => {
      const t = evtType(e)
      return !NOISE.has(t) && !SKIP_DISPLAY_TYPES.has(t)
    })
  }, [events])

  if (events.length === 0) return null

  return (
    <div className="mb-2">
      {/* Collapsed summary bar */}
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className={cn(
          'w-full flex items-center gap-2 px-3 py-4 rounded-lg text-left transition-all',
          'border text-sm',
          isExpanded
            ? 'bg-gray-50 dark:bg-[#12122a] border-gray-200 dark:border-[#2a2a45]'
            : 'bg-gray-50/50 dark:bg-[#12122a]/50 border-gray-100 dark:border-[#2a2a45]/50 hover:bg-gray-50 dark:hover:bg-[#12122a]',
        )}
      >
        {/* Expand/collapse icon */}
        {isExpanded ? (
          <ChevronDown className="h-3 w-3 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 text-gray-400 shrink-0" />
        )}

        {/* Status icon */}
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />

        {/* Summary text */}
        <span className="text-text-secondary dark:text-gray-400 truncate font-bold">
          Agent Process
        </span>

        {/* Summary badges */}
        <div className="flex items-center gap-1.5 ml-auto shrink-0">
          {summary.toolCalls > 0 && (
            <Badge variant="default" size="sm">
              <Wrench className="h-2.5 w-2.5 mr-0.5" />
              {summary.toolCalls}
            </Badge>
          )}
          {summary.agentsSpawned > 0 && (
            <Badge variant="default" size="sm">
              <Users className="h-2.5 w-2.5 mr-0.5" />
              {summary.agentsSpawned}
            </Badge>
          )}
          {summary.debateRounds > 0 && (
            <Badge variant="default" size="sm">
              🗣️ {summary.debateRounds}
            </Badge>
          )}
          {summary.durationMs && (
            <span className="text-text-muted dark:text-gray-500 flex items-center gap-0.5">
              <Timer className="h-2.5 w-2.5" />
              {formatDuration(summary.durationMs)}
            </span>
          )}
        </div>
      </button>

      {/* Expanded event list — rich cards with full details */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="mt-2 space-y-1.5 max-h-[600px] overflow-y-auto">
              {displayEvents.map((evt, idx) => (
                <EventCard key={`${evtType(evt)}-${idx}`} evt={evt} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
