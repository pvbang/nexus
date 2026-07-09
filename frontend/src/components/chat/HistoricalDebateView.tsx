// ============================================================
// MekongAI Nexus - HistoricalDebateView
// Renders debate events as a professional group-chat UI for
// historical replay. Used in MessageEventExplorer when debate
// events are detected in a message's execution trace.
//
// REDESIGN: Full independent debate view with clear agent
// identification, complete content display, and professional
// group-chat aesthetic matching InlineDebateChatStream.
// ============================================================

import { useMemo } from 'react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import {
  Users, Gavel, CheckCircle2, Swords, Search,
  MessageCircle, Target, Lightbulb, Wrench,
  TrendingUp, Award, ShieldCheck,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────

type ProcessEvent = Record<string, unknown>

interface DebateMessage {
  id: string
  agentName: string
  content: string
  turn: number
  messageType: string
  timestamp: string
  eventType: string
  confidence?: number
  isFinal?: boolean
  toolsUsed?: string[]
  qualityScore?: number
  convergenceScore?: number
  actionType?: string
}

interface HistoricalDebateViewProps {
  events: ProcessEvent[]
}

// ── Helpers ───────────────────────────────────────────────────

function evtType(evt: ProcessEvent): string {
  return String(evt.type || evt.event_type || '')
}

function evtStr(evt: ProcessEvent, key: string): string {
  return String(evt[key] || '')
}

function evtContent(evt: ProcessEvent): string {
  const data = (evt.data as Record<string, unknown>) || {}
  return String(
    evt.content || evt.message || evt.preview ||
    data.content || data.message || data.preview || data.output_preview || ''
  )
}

// ── Message type visual config ────────────────────────────────

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
    color: 'text-amber-700 dark:text-amber-400',
    bgColor: 'bg-amber-50 dark:bg-amber-900/20',
  },
}

/** Color palette for agents — each agent gets a unique, visually distinct color */
const AGENT_PALETTE = [
  {
    name: 'text-violet-700 dark:text-violet-300',
    bg: 'bg-violet-100 dark:bg-violet-900/40',
    border: 'border-violet-200 dark:border-violet-800',
    avatar: 'bg-violet-500 dark:bg-violet-600',
  },
  {
    name: 'text-blue-700 dark:text-blue-300',
    bg: 'bg-blue-100 dark:bg-blue-900/40',
    border: 'border-blue-200 dark:border-blue-800',
    avatar: 'bg-blue-500 dark:bg-blue-600',
  },
  {
    name: 'text-emerald-700 dark:text-emerald-300',
    bg: 'bg-emerald-100 dark:bg-emerald-900/40',
    border: 'border-emerald-200 dark:border-emerald-800',
    avatar: 'bg-emerald-500 dark:bg-emerald-600',
  },
  {
    name: 'text-orange-700 dark:text-orange-300',
    bg: 'bg-orange-100 dark:bg-orange-900/40',
    border: 'border-orange-200 dark:border-orange-800',
    avatar: 'bg-orange-500 dark:bg-orange-600',
  },
  {
    name: 'text-pink-700 dark:text-pink-300',
    bg: 'bg-pink-100 dark:bg-pink-900/40',
    border: 'border-pink-200 dark:border-pink-800',
    avatar: 'bg-pink-500 dark:bg-pink-600',
  },
  {
    name: 'text-cyan-700 dark:text-cyan-300',
    bg: 'bg-cyan-100 dark:bg-cyan-900/40',
    border: 'border-cyan-200 dark:border-cyan-800',
    avatar: 'bg-cyan-500 dark:bg-cyan-600',
  },
]

function getAgentColor(agentName: string, agentMap: Map<string, number>) {
  if (!agentMap.has(agentName)) {
    agentMap.set(agentName, agentMap.size)
  }
  return AGENT_PALETTE[agentMap.get(agentName)! % AGENT_PALETTE.length]
}

// ── Debate Events to extract ──────────────────────────────────

const DEBATE_EVENT_TYPES = new Set([
  'debate_message', 'debate_started', 'debate_concluded',
  'moderator_intervention', 'debate_phase', 'synthesis_start', 'synthesis_done',
])

// ── Component ─────────────────────────────────────────────────

export function HistoricalDebateView({ events }: HistoricalDebateViewProps) {
  const agentColorMap = useMemo(() => new Map<string, number>(), [])

  // Extract and organize debate-relevant events
  const { debateItems, meta } = useMemo(() => {
    const items: Array<{
      type: 'message' | 'system' | 'phase'
      data: DebateMessage | { text: string }
    }> = []
    let totalTurns = 0
    let agents: string[] = []
    let concluded = false

    for (const evt of events) {
      const t = evtType(evt)
      if (!DEBATE_EVENT_TYPES.has(t)) continue

      const data = (evt.data as Record<string, unknown>) || {}

      if (t === 'debate_started') {
        agents = ((evt.agents || data.agents) as string[] | undefined) || []
        items.push({
          type: 'system',
          data: { text: `🎯 Debate started${agents.length ? ` — ${agents.length} agents: ${agents.join(', ')}` : ''}` },
        })
      } else if (t === 'debate_phase') {
        const phase = evtStr(evt, 'phase') || String(data.phase || '')
        items.push({
          type: 'phase',
          data: { text: `📋 Phase: ${phase}` },
        })
      } else if (t === 'debate_message') {
        const content = evtContent(evt)
        if (!content || content.length < 3) continue
        const turn = Number(evt.turn || data.turn || 0)
        totalTurns = Math.max(totalTurns, turn)
        items.push({
          type: 'message',
          data: {
            id: String(evt.sequence || evt.id || Math.random()),
            agentName: evtStr(evt, 'agent_name') || String(data.agent_name || 'Agent'),
            content,
            turn,
            messageType: evtStr(evt, 'message_type') || String(data.message_type || 'opinion'),
            timestamp: String(evt.created_at || ''),
            eventType: t,
            confidence: Number(evt.confidence || data.confidence || 0) || undefined,
            isFinal: !!(evt.is_final || data.is_final),
            toolsUsed: (evt.tools_used || data.tools_used || []) as string[],
            qualityScore: Number(evt.quality_score || data.quality_score || 0) || undefined,
            convergenceScore: Number(evt.convergence_score || data.convergence_score || 0) || undefined,
            actionType: String(evt.action_type || data.action_type || evt.action || data.action || '') || undefined,
          },
        })
      } else if (t === 'moderator_intervention') {
        const content = evtContent(evt)
        items.push({
          type: 'message',
          data: {
            id: String(evt.sequence || Math.random()),
            agentName: 'Moderator',
            content: content || String(data.action || 'intervention'),
            turn: Number(evt.turn || data.turn || 0),
            messageType: 'moderator',
            timestamp: String(evt.created_at || ''),
            eventType: t,
            qualityScore: Number(evt.quality_score || data.quality_score || 0) || undefined,
            convergenceScore: Number(evt.convergence_score || data.convergence_score || 0) || undefined,
            actionType: String(evt.action || data.action || evt.action_type || data.action_type || '') || undefined,
          },
        })
      } else if (t === 'synthesis_start') {
        items.push({
          type: 'system',
          data: { text: '⚗️ Synthesizing results...' },
        })
      } else if (t === 'synthesis_done') {
        items.push({
          type: 'system',
          data: { text: '✨ Synthesis complete' },
        })
      } else if (t === 'debate_concluded') {
        concluded = true
        totalTurns = Number(evt.total_turns || data.total_turns || totalTurns)
        const concludedBy = String(evt.concluded_by || data.concluded_by || '')
        const convergenceScore = Number(evt.convergence_score || data.convergence_score || 0)
        items.push({
          type: 'system',
          data: {
            text: `🏁 Debate concluded${totalTurns ? ` — ${totalTurns} turns` : ''}${concludedBy ? ` · ended by ${concludedBy}` : ''}${convergenceScore ? ` · convergence ${(convergenceScore * 100).toFixed(0)}%` : ''}`,
          },
        })
      }
    }
    return {
      debateItems: items,
      meta: { totalTurns, agents, concluded, messageCount: items.filter(i => i.type === 'message').length },
    }
  }, [events])

  if (debateItems.length === 0) {
    return (
      <div className="text-center text-gray-400 text-[11px] py-4">
        No debate messages found.
      </div>
    )
  }

  return (
    <div className="mt-3 rounded-2xl border-2 border-violet-200 dark:border-violet-800/60
      bg-white dark:bg-gray-950 shadow-md overflow-hidden">

      {/* Header */}
      <div className="px-4 py-3 border-b border-violet-200 dark:border-violet-800/60
        bg-gradient-to-r from-violet-50 to-indigo-50 dark:from-violet-950/40 dark:to-indigo-950/40">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-violet-500 dark:bg-violet-600
            flex items-center justify-center shadow-sm">
            <Users className="w-4 h-4 text-white" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h4 className="text-[13px] font-bold text-gray-900 dark:text-gray-100">
                Multi-Agent Debate
              </h4>
              <span className={cn(
                'inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full',
                meta.concluded
                  ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                  : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400',
              )}>
                {meta.concluded ? <CheckCircle2 className="w-3 h-3" /> : <ShieldCheck className="w-3 h-3" />}
                {meta.concluded ? 'Concluded' : 'In Progress'}
              </span>
            </div>
            <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5">
              {meta.messageCount} messages · {meta.totalTurns} turns
              {meta.agents.length > 0 && ` · ${meta.agents.length} agents`}
            </p>
          </div>
        </div>
      </div>

      {/* Message list */}
      <div className="px-4 py-3 space-y-3 max-h-[70vh] overflow-y-auto">
        {debateItems.map((item, idx) => {
          if (item.type === 'system') {
            const { text } = item.data as { text: string }
            return (
              <div key={`sys-${idx}`} className="flex justify-center py-1">
                <span className="text-[10px] text-gray-500 dark:text-gray-400
                  bg-gray-100 dark:bg-gray-800 px-3 py-1.5 rounded-full
                  border border-gray-200 dark:border-gray-700 font-medium">
                  {text}
                </span>
              </div>
            )
          }

          if (item.type === 'phase') {
            const { text } = item.data as { text: string }
            return (
              <div key={`phase-${idx}`} className="flex justify-center py-1">
                <span className="text-[10px] font-semibold text-blue-600 dark:text-blue-400
                  bg-blue-50 dark:bg-blue-900/20 px-3 py-1.5 rounded-full
                  border border-blue-200 dark:border-blue-800">
                  {text}
                </span>
              </div>
            )
          }

          // Message bubble
          const msg = item.data as DebateMessage
          const colors = getAgentColor(msg.agentName, agentColorMap)
          const isModerator = msg.messageType === 'moderator' || msg.agentName === 'Moderator'
          const typeConfig = MESSAGE_TYPE_CONFIG[msg.messageType] || MESSAGE_TYPE_CONFIG.opinion

          // Moderator card — special styling
          if (isModerator) {
            return (
              <div key={msg.id} className="flex justify-center my-2">
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
                    {msg.actionType && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full
                        bg-amber-200/70 dark:bg-amber-800/40
                        text-amber-700 dark:text-amber-300 font-medium">
                        {msg.actionType}
                      </span>
                    )}
                    <span className="text-[10px] text-amber-600/60 dark:text-amber-400/60 ml-auto">
                      Turn {msg.turn}
                    </span>
                  </div>
                  <div className="text-[12px] text-amber-900 dark:text-amber-100 leading-relaxed pl-8
                    prose prose-sm dark:prose-invert max-w-none">
                    <MarkdownRenderer content={msg.content} />
                  </div>
                  {(msg.qualityScore != null || msg.convergenceScore != null) && (
                    <div className="flex gap-4 mt-2 pl-8">
                      {msg.qualityScore != null && msg.qualityScore > 0 && (
                        <div className="flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-300">
                          <Award className="w-3 h-3" />
                          Quality: {(msg.qualityScore * 100).toFixed(0)}%
                        </div>
                      )}
                      {msg.convergenceScore != null && msg.convergenceScore > 0 && (
                        <div className="flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-300">
                          <TrendingUp className="w-3 h-3" />
                          Convergence: {(msg.convergenceScore * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          }

          // Regular agent message
          return (
            <div key={msg.id} className="flex gap-3 items-start">
              {/* Avatar */}
              <div className={cn(
                'w-8 h-8 rounded-full flex items-center justify-center',
                'text-white text-[11px] font-bold shrink-0 shadow-sm',
                colors.avatar,
              )}>
                {msg.agentName.charAt(0).toUpperCase()}
              </div>

              {/* Message content */}
              <div className="flex-1 min-w-0">
                {/* Header */}
                <div className="flex items-center gap-2 mb-1">
                  <span className={cn('text-[12px] font-bold', colors.name)}>
                    {msg.agentName}
                  </span>
                  {msg.turn > 0 && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500 tabular-nums">
                      Turn {msg.turn}
                    </span>
                  )}
                </div>

                {/* Content card */}
                <div className={cn(
                  'rounded-xl px-4 py-3 border shadow-sm',
                  'bg-white dark:bg-gray-900/60',
                  colors.border,
                )}>
                  {/* Message type + final badge */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className={cn(
                      'inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full',
                      typeConfig.bgColor, typeConfig.color,
                    )}>
                      {typeConfig.icon}
                      {typeConfig.label}
                    </span>
                    {msg.isFinal && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold px-2 py-0.5
                        rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-300">
                        <ShieldCheck className="w-3 h-3" />
                        Final
                      </span>
                    )}
                    {msg.confidence != null && msg.confidence > 0 && (
                      <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-auto">
                        Confidence: {(msg.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>

                  {/* Full content with markdown */}
                  <div className="text-[12px] text-gray-800 dark:text-gray-200 leading-relaxed
                    prose prose-sm dark:prose-invert max-w-none
                    prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5
                    prose-headings:text-[13px] prose-headings:my-1.5">
                    <MarkdownRenderer content={msg.content} />
                  </div>

                  {/* Tools used */}
                  {msg.toolsUsed && msg.toolsUsed.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2.5 pt-2 border-t border-gray-100 dark:border-gray-800">
                      <Wrench className="w-3 h-3 text-gray-400 dark:text-gray-500 shrink-0 mt-0.5" />
                      {msg.toolsUsed.map((tool, i) => (
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
            </div>
          )
        })}
      </div>
    </div>
  )
}
