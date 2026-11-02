// ============================================================
// Nexus - InlineDebateChatStream
// Renders chat-based debate (debate_message events) inline in
// the main chat area as a self-contained, visually distinct panel.
// Works for both live streaming and history replay.
//
// REDESIGN: Full independent debate view with clear agent
// identification, complete content display, and professional
// group-chat aesthetic.
// ============================================================

import { useEffect, useRef, useMemo } from 'react'
import {
  Users, MessageSquare, Swords, CheckCircle2,
  Wrench, Gavel, Target, Lightbulb, Search,
  Activity, ShieldCheck, MessageCircle,
  TrendingUp, Award, Zap,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import { useExecutionStore } from '../../stores/executionStore'
import { useProcessStore } from '../../stores/processStore'
import { AgentToolEventsPanel } from './AgentToolEventsPanel'
import type { AgentToolEvent } from './AgentToolEventsPanel'
import type { ExecutionEvent } from '../../types/execution'

// ── Types ─────────────────────────────────────────────────────

interface DebateMessage {
  turn: number
  agent_name: string
  agent_role: string
  content: string
  message_type: string
  tools_used: string[]
  confidence?: number
  is_final?: boolean
  quality_score?: number
  convergence_score?: number
  action_type?: string
  timestamp: number
}

interface DebateState {
  messages: DebateMessage[]
  isActive: boolean
  agents: string[]
  maxTurns: number
  totalTurns: number
  concludedBy?: string
  convergenceScore?: number
  /** Tool events grouped by agent_name → turn → events */
  toolEventsByAgent: Map<string, AgentToolEvent[]>
}

interface InlineDebateChatStreamProps {
  sessionId: string
}

// ── Constants ─────────────────────────────────────────────────

const MESSAGE_TYPE_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string; bgColor: string }> = {
  opinion: {
    icon: <MessageCircle className="w-3 h-3" />,
    label: 'Opinion',
    color: 'text-blue-600 dark:text-blue-400',
    bgColor: 'bg-blue-50 dark:bg-blue-900/20',
  },
  evidence: {
    icon: <Search className="w-3 h-3" />,
    label: 'Evidence',
    color: 'text-emerald-600 dark:text-emerald-400',
    bgColor: 'bg-emerald-50 dark:bg-emerald-900/20',
  },
  challenge: {
    icon: <Swords className="w-3 h-3" />,
    label: 'Challenge',
    color: 'text-red-600 dark:text-red-400',
    bgColor: 'bg-red-50 dark:bg-red-900/20',
  },
  agreement: {
    icon: <CheckCircle2 className="w-3 h-3" />,
    label: 'Agreement',
    color: 'text-green-600 dark:text-green-400',
    bgColor: 'bg-green-50 dark:bg-green-900/20',
  },
  question: {
    icon: <MessageSquare className="w-3 h-3" />,
    label: 'Question',
    color: 'text-amber-600 dark:text-amber-400',
    bgColor: 'bg-amber-50 dark:bg-amber-900/20',
  },
  tool_result: {
    icon: <Wrench className="w-3 h-3" />,
    label: 'Tool Result',
    color: 'text-purple-600 dark:text-purple-400',
    bgColor: 'bg-purple-50 dark:bg-purple-900/20',
  },
  synthesis: {
    icon: <Lightbulb className="w-3 h-3" />,
    label: 'Synthesis',
    color: 'text-cyan-600 dark:text-cyan-400',
    bgColor: 'bg-cyan-50 dark:bg-cyan-900/20',
  },
  final_position: {
    icon: <Target className="w-3 h-3" />,
    label: 'Final Position',
    color: 'text-indigo-600 dark:text-indigo-400',
    bgColor: 'bg-indigo-50 dark:bg-indigo-900/20',
  },
  moderator: {
    icon: <Gavel className="w-3 h-3" />,
    label: 'Moderator',
    color: 'text-yellow-700 dark:text-yellow-400',
    bgColor: 'bg-yellow-50 dark:bg-yellow-900/20',
  },
}

/** Color palette for agents — each agent gets a unique, visually distinct color */
const AGENT_PALETTE = [
  {
    name: 'text-violet-700 dark:text-violet-300',
    bg: 'bg-violet-100 dark:bg-violet-900/40',
    border: 'border-violet-300 dark:border-violet-700',
    ring: 'ring-violet-200 dark:ring-violet-800',
    avatar: 'bg-violet-500 dark:bg-violet-600',
  },
  {
    name: 'text-blue-700 dark:text-blue-300',
    bg: 'bg-blue-100 dark:bg-blue-900/40',
    border: 'border-blue-300 dark:border-blue-700',
    ring: 'ring-blue-200 dark:ring-blue-800',
    avatar: 'bg-blue-500 dark:bg-blue-600',
  },
  {
    name: 'text-emerald-700 dark:text-emerald-300',
    bg: 'bg-emerald-100 dark:bg-emerald-900/40',
    border: 'border-emerald-300 dark:border-emerald-700',
    ring: 'ring-emerald-200 dark:ring-emerald-800',
    avatar: 'bg-emerald-500 dark:bg-emerald-600',
  },
  {
    name: 'text-orange-700 dark:text-orange-300',
    bg: 'bg-orange-100 dark:bg-orange-900/40',
    border: 'border-orange-300 dark:border-orange-700',
    ring: 'ring-orange-200 dark:ring-orange-800',
    avatar: 'bg-orange-500 dark:bg-orange-600',
  },
  {
    name: 'text-pink-700 dark:text-pink-300',
    bg: 'bg-pink-100 dark:bg-pink-900/40',
    border: 'border-pink-300 dark:border-pink-700',
    ring: 'ring-pink-200 dark:ring-pink-800',
    avatar: 'bg-pink-500 dark:bg-pink-600',
  },
  {
    name: 'text-cyan-700 dark:text-cyan-300',
    bg: 'bg-cyan-100 dark:bg-cyan-900/40',
    border: 'border-cyan-300 dark:border-cyan-700',
    ring: 'ring-cyan-200 dark:ring-cyan-800',
    avatar: 'bg-cyan-500 dark:bg-cyan-600',
  },
]

// ── Helper: Extract debate state from execution events ─────────

function extractDebateState(events: ExecutionEvent[]): DebateState | null {
  const messages: DebateMessage[] = []
  let isActive = false
  let agents: string[] = []
  let maxTurns = 30
  let totalTurns = 0
  let concludedBy: string | undefined
  let convergenceScore: number | undefined
  let hasDebateEvents = false
  const toolEventsByAgent = new Map<string, AgentToolEvent[]>()

  // Temporary map to pair tool_call with tool_result
  const pendingToolCalls = new Map<string, Partial<AgentToolEvent>>()

  for (const evt of events) {
    const t = String((evt as any).type || '')

    if (t === 'debate_started') {
      hasDebateEvents = true
      isActive = true
      // REACT-310-FIX: agents can be objects [{name, role}] — normalize to string[]
      const rawAgents = (evt as any).agents || []
      agents = Array.isArray(rawAgents)
        ? rawAgents.map((a: unknown) => typeof a === 'string' ? a : String((a as any)?.name || 'Agent'))
        : []
      maxTurns = Number((evt as any).max_turns) || 30
    } else if (t === 'debate_message') {
      hasDebateEvents = true
      const e = evt as any
      // REACT-310-FIX: Force all text fields to string to prevent object-as-child
      const content = typeof e.content === 'string' ? e.content
        : (typeof e.content === 'object' && e.content !== null) ? JSON.stringify(e.content) : String(e.content || '')
      messages.push({
        turn: Number(e.turn) || 0,
        agent_name: String(e.agent_name || 'Agent'),
        agent_role: String(e.agent_role || ''),
        content,
        message_type: String(e.message_type || 'opinion'),
        tools_used: Array.isArray(e.tools_used) ? e.tools_used.map(String) : [],
        confidence: typeof e.confidence === 'number' ? e.confidence : undefined,
        is_final: !!e.is_final,
        quality_score: typeof e.quality_score === 'number' ? e.quality_score : undefined,
        convergence_score: typeof e.convergence_score === 'number' ? e.convergence_score : undefined,
        action_type: e.action_type ? String(e.action_type) : undefined,
        timestamp: Number(e.timestamp) || Date.now(),
      })
      totalTurns = Math.max(totalTurns, Number(e.turn) || 0)
    } else if (t === 'moderator_intervention') {
      hasDebateEvents = true
      const e = evt as any
      const rawContent = e.message || e.content || ''
      const content = typeof rawContent === 'string' ? rawContent
        : (typeof rawContent === 'object' && rawContent !== null) ? JSON.stringify(rawContent) : String(rawContent)
      messages.push({
        turn: Number(e.turn) || totalTurns,
        agent_name: 'Moderator',
        agent_role: 'moderator',
        content,
        message_type: 'moderator',
        tools_used: [],
        quality_score: typeof e.quality_score === 'number' ? e.quality_score : undefined,
        convergence_score: typeof e.convergence_score === 'number' ? e.convergence_score : undefined,
        action_type: (e.action || e.action_type) ? String(e.action || e.action_type) : undefined,
        timestamp: Number(e.timestamp) || Date.now(),
      })
    } else if (t === 'debate_concluded') {
      hasDebateEvents = true
      isActive = false
      // REACT-310-FIX: Force to string — concluded_by may be object
      const rawConcludedBy = (evt as any).concluded_by
      concludedBy = rawConcludedBy ? String(rawConcludedBy) : undefined
      convergenceScore = typeof (evt as any).convergence_score === 'number'
        ? (evt as any).convergence_score : undefined
      totalTurns = Number((evt as any).total_turns) || totalTurns
    }
    // ── Extract tool events per agent ──
    else if (t === 'debate_tool_call' || t === 'action_tool_call') {
      hasDebateEvents = true
      const e = evt as any
      const agentName = String(e.agent_name || 'Agent')
      const toolEvent: AgentToolEvent = {
        tool: String(e.tool || ''),
        params_preview: e.params_preview ? String(e.params_preview) : undefined,
        turn: Number(e.turn) || 0,
        phase: e.phase ? String(e.phase) : (t === 'action_tool_call' ? 'action' : 'research'),
        timestamp: Number(e.timestamp) || Date.now(),
      }
      // Store pending call (will merge with result)
      const key = `${agentName}:${toolEvent.tool}:${toolEvent.turn}`
      pendingToolCalls.set(key, toolEvent)
      // Add to agent's events list immediately (update on result)
      if (!toolEventsByAgent.has(agentName)) {
        toolEventsByAgent.set(agentName, [])
      }
      toolEventsByAgent.get(agentName)!.push(toolEvent)
    } else if (t === 'debate_tool_result' || t === 'action_tool_result') {
      hasDebateEvents = true
      const e = evt as any
      const agentName = String(e.agent_name || 'Agent')
      const toolName = String(e.tool || '')
      const turn = Number(e.turn) || 0
      const key = `${agentName}:${toolName}:${turn}`

      // Try to merge with pending call
      const pending = pendingToolCalls.get(key)
      if (pending) {
        pending.success = e.success != null ? Boolean(e.success) : undefined
        pending.output_preview = e.output_preview ? String(e.output_preview) : undefined
        pendingToolCalls.delete(key)
      } else {
        // No matching call found — add as standalone result
        const toolEvent: AgentToolEvent = {
          tool: toolName,
          output_preview: e.output_preview ? String(e.output_preview) : undefined,
          success: e.success != null ? Boolean(e.success) : undefined,
          turn,
          phase: e.phase ? String(e.phase) : (t === 'action_tool_result' ? 'action' : 'research'),
          timestamp: Number(e.timestamp) || Date.now(),
        }
        if (!toolEventsByAgent.has(agentName)) {
          toolEventsByAgent.set(agentName, [])
        }
        toolEventsByAgent.get(agentName)!.push(toolEvent)
      }
    }
  }

  if (!hasDebateEvents) return null

  // FIX-1: Sort messages by turn number for correct chronological order
  messages.sort((a, b) => a.turn - b.turn || a.timestamp - b.timestamp)

  // FIX-1: Dedup — same turn + same agent + same content prefix = duplicate
  const deduped: DebateMessage[] = []
  for (const msg of messages) {
    const isDuplicate = deduped.some(
      (prev) =>
        prev.turn === msg.turn &&
        prev.agent_name === msg.agent_name &&
        prev.content.slice(0, 80) === msg.content.slice(0, 80),
    )
    if (!isDuplicate) {
      deduped.push(msg)
    }
  }

  return { messages: deduped, isActive, agents, maxTurns, totalTurns, concludedBy, convergenceScore, toolEventsByAgent }
}

// ── Moderator Intervention Card ───────────────────────────────

function ModeratorCard({ message }: { message: DebateMessage }) {
  return (
    <div className="flex justify-center my-3">
      <div className="w-full max-w-[95%] bg-amber-50/80 dark:bg-amber-950/30
        border border-amber-200 dark:border-amber-800/60
        rounded-xl px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2 mb-1.5">
          <div className="w-6 h-6 rounded-full bg-amber-400 dark:bg-amber-600
            flex items-center justify-center">
            <Gavel className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-[12px] font-bold text-amber-800 dark:text-amber-200">
            Moderator
          </span>
          {message.action_type && (
            <span className="text-[10px] px-2 py-0.5 rounded-full
              bg-amber-200/70 dark:bg-amber-800/40
              text-amber-700 dark:text-amber-300 font-medium">
              {message.action_type}
            </span>
          )}
          <span className="text-[10px] text-amber-600/60 dark:text-amber-400/60 ml-auto">
            Turn {message.turn}
          </span>
        </div>
        <div className="text-[12px] text-amber-900 dark:text-amber-100 leading-relaxed pl-8">
          <MarkdownRenderer content={message.content} />
        </div>
        {(message.quality_score != null || message.convergence_score != null) && (
          <div className="flex gap-4 mt-2 pl-8">
            {message.quality_score != null && (
              <div className="flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-300">
                <Award className="w-3 h-3" />
                Quality: {(message.quality_score * 100).toFixed(0)}%
              </div>
            )}
            {message.convergence_score != null && (
              <div className="flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-300">
                <TrendingUp className="w-3 h-3" />
                Convergence: {(message.convergence_score * 100).toFixed(0)}%
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Single Message Bubble ─────────────────────────────────────

function DebateMessageBubble({
  message,
  agentColor,
  showAvatar,
  toolEvents,
}: {
  message: DebateMessage
  agentColor: typeof AGENT_PALETTE[0]
  showAvatar: boolean
  toolEvents?: AgentToolEvent[]
}) {
  const typeConfig = MESSAGE_TYPE_CONFIG[message.message_type] || MESSAGE_TYPE_CONFIG.opinion

  if (message.message_type === 'moderator') {
    return <ModeratorCard message={message} />
  }

  const hasToolEvents = toolEvents && toolEvents.length > 0

  return (
    <div className="flex gap-3 items-start mb-3 group">
      {/* Avatar column */}
      <div className="w-8 shrink-0 flex flex-col items-center">
        {showAvatar ? (
          <div className={cn(
            'w-8 h-8 rounded-full flex items-center justify-center',
            'text-white text-[11px] font-bold shadow-sm',
            agentColor.avatar,
          )}>
            {message.agent_name.charAt(0).toUpperCase()}
          </div>
        ) : (
          <div className="w-8 h-8" />
        )}
      </div>

      {/* Message content + tool panel row */}
      <div className="flex-1 min-w-0 flex gap-3">
        {/* Left: message content */}
        <div className="flex-1 min-w-0">
          {/* Header — only show for first message in group */}
          {showAvatar && (
            <div className="flex items-center gap-2 mb-1">
              <span className={cn('text-[12px] font-bold', agentColor.name)}>
                {message.agent_name}
              </span>
              {message.agent_role && (
                <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">
                  {message.agent_role}
                </span>
              )}
              <span className="text-[10px] text-gray-400 dark:text-gray-600 ml-auto tabular-nums">
                Turn {message.turn}
              </span>
            </div>
          )}

          {/* Content card */}
          <div className={cn(
            'rounded-xl px-4 py-3 border shadow-sm',
            'bg-white dark:bg-gray-900/60',
            agentColor.border,
          )}>
            {/* Message type badge */}
            <div className="flex items-center gap-2 mb-2">
              <span className={cn(
                'inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full',
                typeConfig.bgColor, typeConfig.color,
              )}>
                {typeConfig.icon}
                {typeConfig.label}
              </span>
              {message.is_final && (
                <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold px-2 py-0.5
                  rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-300">
                  <ShieldCheck className="w-3 h-3" />
                  Final
                </span>
              )}
              {message.confidence != null && message.confidence > 0 && (
                <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-auto">
                  Confidence: {(message.confidence * 100).toFixed(0)}%
                </span>
              )}
            </div>

            {/* Full content — rendered as markdown */}
            <div className="text-[12px] text-gray-800 dark:text-gray-200 leading-relaxed prose prose-sm
              dark:prose-invert max-w-none
              prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5
              prose-headings:text-[13px] prose-headings:my-1.5">
              <MarkdownRenderer content={message.content} />
            </div>

            {/* Tools used (simple badges from debate_message) */}
            {message.tools_used.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2.5 pt-2 border-t border-gray-100 dark:border-gray-800">
                <Wrench className="w-3 h-3 text-gray-400 dark:text-gray-500 shrink-0 mt-0.5" />
                {message.tools_used.map((tool, i) => (
                  <span key={i} className="text-[10px] px-2 py-0.5 rounded-md
                    bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400
                    border border-gray-200 dark:border-gray-700 font-mono">
                    {tool}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: Tool events panel */}
        {hasToolEvents && (
          <AgentToolEventsPanel
            events={toolEvents}
            agentName={message.agent_name}
          />
        )}
      </div>
    </div>
  )
}

// ── Debate Header / Stats ─────────────────────────────────────

function DebateHeader({
  debateState,
  agentColorMap,
}: {
  debateState: DebateState
  agentColorMap: Map<string, typeof AGENT_PALETTE[0]>
}) {
  const progress = debateState.maxTurns > 0
    ? Math.min(100, (debateState.totalTurns / debateState.maxTurns) * 100)
    : 0

  return (
    <div className="px-5 py-4 border-b border-violet-200 dark:border-violet-800/60
      bg-gradient-to-r from-violet-50 to-indigo-50 dark:from-violet-950/40 dark:to-indigo-950/40">
      {/* Title row */}
      <div className="flex items-center gap-2.5 mb-3">
        <div className="w-9 h-9 rounded-xl bg-violet-500 dark:bg-violet-600
          flex items-center justify-center shadow-sm">
          <Users className="w-5 h-5 text-white" />
        </div>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[14px] font-bold text-gray-900 dark:text-gray-100">
              Multi-Agent Debate
            </h3>
            {debateState.isActive ? (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5
                rounded-full bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                <Activity className="w-3 h-3 animate-pulse" />
                Live
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5
                rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                <CheckCircle2 className="w-3 h-3" />
                Concluded
              </span>
            )}
          </div>
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">
            {debateState.messages.length} messages · {debateState.totalTurns} turns
            {debateState.concludedBy && ` · ended by ${debateState.concludedBy}`}
          </p>
        </div>
      </div>

      {/* Participating agents */}
      <div className="flex flex-wrap gap-2 mb-3">
        {Array.from(agentColorMap.entries()).map(([agentName, color]) => (
          <div key={agentName} className={cn(
            'inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-lg border',
            color.bg, color.border, color.name,
          )}>
            <div className={cn('w-4 h-4 rounded-full flex items-center justify-center text-[9px] text-white font-bold', color.avatar)}>
              {agentName.charAt(0).toUpperCase()}
            </div>
            {agentName}
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full bg-violet-200/60 dark:bg-violet-800/40 overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-700 ease-out',
              debateState.isActive
                ? 'bg-gradient-to-r from-violet-400 to-indigo-400 animate-pulse'
                : 'bg-gradient-to-r from-violet-500 to-indigo-500',
            )}
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="text-[10px] text-gray-500 dark:text-gray-400 tabular-nums shrink-0">
          {debateState.totalTurns}/{debateState.maxTurns}
        </span>
      </div>
    </div>
  )
}

// ── Debate Conclusion Footer ──────────────────────────────────

function DebateConclusion({ debateState }: { debateState: DebateState }) {
  if (debateState.isActive) return null
  if (!debateState.concludedBy && debateState.convergenceScore == null) return null

  return (
    <div className="px-5 py-3 border-t border-violet-200 dark:border-violet-800/60
      bg-gradient-to-r from-green-50/50 to-emerald-50/50
      dark:from-green-950/20 dark:to-emerald-950/20">
      <div className="flex items-center gap-3">
        <div className="w-7 h-7 rounded-full bg-green-500 dark:bg-green-600
          flex items-center justify-center">
          <CheckCircle2 className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1">
          <p className="text-[12px] font-semibold text-green-800 dark:text-green-200">
            Debate Concluded
          </p>
          <div className="flex items-center gap-4 mt-0.5">
            {debateState.concludedBy && (
              <span className="text-[11px] text-green-700 dark:text-green-300">
                Ended by: <strong>{debateState.concludedBy}</strong>
              </span>
            )}
            {debateState.convergenceScore != null && (
              <span className="flex items-center gap-1 text-[11px] text-green-700 dark:text-green-300">
                <TrendingUp className="w-3 h-3" />
                Convergence: <strong>{(debateState.convergenceScore * 100).toFixed(0)}%</strong>
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────

export function InlineDebateChatStream({ sessionId }: InlineDebateChatStreamProps) {
  const executionEvents = useExecutionStore((s) => s.eventsBySession[sessionId]) ?? []
  const processStage = useProcessStore((s) => s.stage)
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevMessageCountRef = useRef(0)

  // Extract debate state from events
  const debateState = useMemo(() => {
    const state = extractDebateState(executionEvents)
    if (!state) return null
    // FIX: If process completed (stage moved past debating, or completion events exist),
    // debate is no longer active — hide the "Agents đang tranh luận..." spinner
    if (state.isActive) {
      // Check 1: Process stage moved past debating
      // DEBATE-VIS-FIX: Removed '' from completedStages. Empty string means stage not yet set
      // (initial state), NOT "completed". Previously this caused debate to show "Concluded"
      // immediately when processStage hadn't been updated to 'debating' yet.
      // Also removed 'synthesizing' — synthesis happens AFTER debate concludes naturally,
      // so debate_concluded event (Check 2) handles that transition.
      const completedStages = ['completed', 'done']
      if (completedStages.includes(processStage) && state.messages.length > 0) {
        state.isActive = false
      }
      // Check 2: Explicit completion events in store
      const hasCompletionSignal = executionEvents.some((evt: any) => {
        const t = String(evt?.type || '')
        return t === 'task_complete' || t === 'synthesis_done' || t === 'result' || t === 'debate_concluded'
      })
      if (hasCompletionSignal) {
        state.isActive = false
      }
    }
    return state
  }, [executionEvents, processStage])

  // Build agent color map (stable assignment order)
  // HOOKS-FIX: All hooks must be called unconditionally (before any return)
  const agentColorMap = useMemo(() => {
    if (!debateState || debateState.messages.length === 0) {
      return new Map<string, typeof AGENT_PALETTE[0]>()
    }
    const map = new Map<string, typeof AGENT_PALETTE[0]>()
    let idx = 0
    for (const msg of debateState.messages) {
      if (!map.has(msg.agent_name) && msg.agent_name !== 'Moderator') {
        map.set(msg.agent_name, AGENT_PALETTE[idx % AGENT_PALETTE.length])
        idx++
      }
    }
    // Also include agents from debate_started event
    for (const agentName of debateState.agents) {
      const name = typeof agentName === 'string' ? agentName : String((agentName as any).name || 'Agent')
      if (!map.has(name) && name !== 'Moderator') {
        map.set(name, AGENT_PALETTE[idx % AGENT_PALETTE.length])
        idx++
      }
    }
    return map
  }, [debateState])

  // Determine which messages should show avatars (first in consecutive group from same agent)
  const shouldShowAvatar = useMemo(() => {
    if (!debateState || debateState.messages.length === 0) return []
    return debateState.messages.map((msg, i) => {
      if (i === 0) return true
      return debateState.messages[i - 1].agent_name !== msg.agent_name
    })
  }, [debateState])

  // Auto-scroll when new messages arrive
  useEffect(() => {
    if (debateState && debateState.messages.length > prevMessageCountRef.current) {
      prevMessageCountRef.current = debateState.messages.length
      if (scrollRef.current) {
        requestAnimationFrame(() => {
          scrollRef.current?.scrollTo({
            top: scrollRef.current.scrollHeight,
            behavior: 'smooth',
          })
        })
      }
    }
  }, [debateState])

  // Don't render if no debate events (conditional return AFTER all hooks)
  if (!debateState || debateState.messages.length === 0) {
    return null
  }

  return (
    <div className="mx-1 my-4 rounded-2xl border-2 border-violet-200 dark:border-violet-800/70
      bg-white dark:bg-gray-950
      shadow-lg shadow-violet-100/50 dark:shadow-violet-900/20
      overflow-hidden">

      {/* Header with stats */}
      <DebateHeader debateState={debateState} agentColorMap={agentColorMap} />

      {/* Messages area */}
      <div
        ref={scrollRef}
        className="px-4 py-4 overflow-y-auto"
        style={{ maxHeight: '70vh' }}
      >
        {debateState.messages.map((msg, i) => {
          // Get tool events for this agent at this turn
          const agentTools = debateState.toolEventsByAgent.get(msg.agent_name) || []
          const turnTools = agentTools.filter((te) => te.turn === msg.turn)

          return (
            <DebateMessageBubble
              key={`debate-msg-${msg.turn}-${msg.agent_name}-${i}`}
              message={msg}
              agentColor={agentColorMap.get(msg.agent_name) || AGENT_PALETTE[0]}
              showAvatar={shouldShowAvatar[i]}
              toolEvents={turnTools.length > 0 ? turnTools : undefined}
            />
          )
        })}

        {/* Live typing indicator */}
        {debateState.isActive && (
          <div className="flex items-center gap-3 mt-3 ml-11">
            <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl
              bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
              <Zap className="w-3 h-3 text-violet-500 animate-pulse" />
              <div className="flex gap-1">
                <span className="w-2 h-2 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <span className="text-[11px] text-gray-500 dark:text-gray-400 ml-1">
                Agents đang tranh luận...
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Conclusion footer */}
      <DebateConclusion debateState={debateState} />
    </div>
  )
}
