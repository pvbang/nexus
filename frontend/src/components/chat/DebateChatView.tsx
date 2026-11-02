// ============================================================
// Nexus - DebateChatView
// Group chat UI for the new conversational debate system.
// Renders debate messages as a real-time chat between agents,
// with avatar, role badges, tool indicators, and references.
// ============================================================

import { useState, useEffect, useRef } from 'react'
import {
  Users, MessageSquare, Swords, CheckCircle2, HelpCircle,
  Wrench, Gavel, Target, AlertTriangle, ChevronDown,
  Bot, Shield, Lightbulb, Search, Scale,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'

// ── Types ─────────────────────────────────────────────────────

export interface DebateChatMessage {
  turn: number
  agent_name: string
  agent_role: string
  content: string
  message_type: string // opinion | evidence | challenge | agreement | question | tool_result | synthesis | final_position | moderator
  references: number[] // Turn numbers referenced
  tools_used: string[]
  evidence_items: string[]
  confidence: number
  timestamp: string
  is_final?: boolean
  // Moderator specific
  quality_score?: number
  convergence_score?: number
  action_type?: string
}

export interface DebateChatState {
  messages: DebateChatMessage[]
  isActive: boolean
  totalTurns: number
  maxTurns: number
  budgetRemaining: number
  agents: Array<{ name: string; role: string; debate_role?: string }>
  concludedBy?: string
  // Real-time activity tracking (shows what's happening between messages)
  currentSpeaker?: string
  currentSpeakerRole?: string
  currentActivity?: string // 'planning' | 'researching' | 'acting' | 'arguing' | null
  currentTool?: string // Current tool being called
  // AUDIT-FIX MEDIUM-2: Phase and reflection tracking
  currentPhase?: string // 'preparation' | 'final_positions' | 'synthesis'
  phaseStatus?: string // 'active' | 'complete'
  reflectionData?: {
    progressScore: number
    sufficient: boolean
    stagnating: boolean
    recommendation: string
  }
}

interface DebateChatViewProps {
  state: DebateChatState
}

// ── Constants ─────────────────────────────────────────────────

const MESSAGE_TYPE_CONFIG: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  opinion: { icon: <MessageSquare className="w-3 h-3" />, color: 'text-blue-500', label: 'Opinion' },
  evidence: { icon: <Search className="w-3 h-3" />, color: 'text-emerald-500', label: 'Evidence' },
  challenge: { icon: <Swords className="w-3 h-3" />, color: 'text-red-500', label: 'Challenge' },
  agreement: { icon: <CheckCircle2 className="w-3 h-3" />, color: 'text-green-500', label: 'Agreement' },
  question: { icon: <HelpCircle className="w-3 h-3" />, color: 'text-amber-500', label: 'Question' },
  tool_result: { icon: <Wrench className="w-3 h-3" />, color: 'text-purple-500', label: 'Tool Result' },
  synthesis: { icon: <Lightbulb className="w-3 h-3" />, color: 'text-cyan-500', label: 'Synthesis' },
  final_position: { icon: <Target className="w-3 h-3" />, color: 'text-indigo-500', label: 'Final Position' },
  moderator: { icon: <Gavel className="w-3 h-3" />, color: 'text-yellow-600', label: 'Moderator' },
}

const ROLE_COLORS: Record<string, string> = {
  advocate: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20',
  critic: 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20',
  synthesizer: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20',
  researcher: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20',
  devils_advocate: 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20',
  domain_expert: 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20',
  moderator: 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border-yellow-500/20',
}

const ROLE_ICONS: Record<string, React.ReactNode> = {
  advocate: <Scale className="w-3.5 h-3.5" />,
  critic: <Shield className="w-3.5 h-3.5" />,
  synthesizer: <Lightbulb className="w-3.5 h-3.5" />,
  researcher: <Search className="w-3.5 h-3.5" />,
  devils_advocate: <AlertTriangle className="w-3.5 h-3.5" />,
  domain_expert: <Bot className="w-3.5 h-3.5" />,
  moderator: <Gavel className="w-3.5 h-3.5" />,
}

// ── Avatar Component ──────────────────────────────────────────

function AgentAvatar({ name, role }: { name: string; role: string }) {
  const initials = name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
  const roleColor = ROLE_COLORS[role] || 'bg-gray-500/10 text-gray-600 border-gray-500/20'

  return (
    <div className={cn(
      'w-8 h-8 rounded-full flex items-center justify-center border text-xs font-bold shrink-0',
      roleColor,
    )}>
      {ROLE_ICONS[role] || initials}
    </div>
  )
}

// ── Single Message Component ──────────────────────────────────

function ChatMessageBubble({
  message,
  isConsecutive,
}: {
  message: DebateChatMessage
  isConsecutive: boolean
}) {
  const [expanded, setExpanded] = useState(true)
  const isModerator = message.message_type === 'moderator'
  const typeConfig = MESSAGE_TYPE_CONFIG[message.message_type] || MESSAGE_TYPE_CONFIG.opinion
  const debateRole = message.agent_role // This maps to debate role in our system

  if (isModerator) {
    return (
      <div className="flex justify-center my-3">
        <div className={cn(
          'max-w-[85%] px-4 py-2.5 rounded-lg border text-sm',
          'bg-yellow-50/50 border-yellow-200/50 dark:bg-yellow-900/10 dark:border-yellow-700/30',
        )}>
          <div className="flex items-center gap-2 mb-1">
            <Gavel className="w-3.5 h-3.5 text-yellow-600 dark:text-yellow-400" />
            <span className="text-xs font-semibold text-yellow-700 dark:text-yellow-400">
              Moderator
            </span>
            {message.quality_score != null && (
              <Badge variant="default" size="sm">
                Quality: {(message.quality_score * 100).toFixed(0)}%
              </Badge>
            )}
            {message.convergence_score != null && (
              <Badge variant={message.convergence_score > 0.7 ? 'success' : 'default'} size="sm">
                Convergence: {(message.convergence_score * 100).toFixed(0)}%
              </Badge>
            )}
          </div>
          <div className="text-yellow-800 dark:text-yellow-200/80 text-[13px]">
            <MarkdownRenderer content={message.content} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={cn('flex gap-2.5', isConsecutive ? 'mt-1' : 'mt-4')}>
      {/* Avatar (only show for first message in sequence) */}
      {!isConsecutive ? (
        <AgentAvatar name={message.agent_name} role={debateRole} />
      ) : (
        <div className="w-8 shrink-0" /> // Spacer
      )}

      {/* Message content */}
      <div className="flex-1 min-w-0">
        {/* Header (only show for first message in sequence) */}
        {!isConsecutive && (
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-text-primary dark:text-gray-100">
              {message.agent_name}
            </span>
            <Badge
              variant="default"
              size="sm"
              className={cn('capitalize text-[10px]', ROLE_COLORS[debateRole])}
            >
              {debateRole.replace('_', ' ')}
            </Badge>
            <span className={cn('flex items-center gap-0.5 text-[10px]', typeConfig.color)}>
              {typeConfig.icon}
              {typeConfig.label}
            </span>
            {message.is_final && (
              <Badge variant="success" size="sm">Final</Badge>
            )}
            <span className="text-[10px] text-text-muted dark:text-gray-600 ml-auto">
              Turn {message.turn}
            </span>
          </div>
        )}

        {/* References indicator */}
        {message.references.length > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-text-muted dark:text-gray-500 mb-1">
            <span>↩️ replying to turn {message.references.join(', ')}</span>
          </div>
        )}

        {/* Tools used indicator */}
        {message.tools_used.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 mb-1.5">
            {message.tools_used.map((tool, i) => (
              <span
                key={`${tool}-${i}`}
                className={cn(
                  'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px]',
                  'bg-purple-500/10 text-purple-600 dark:text-purple-400',
                )}
              >
                <Wrench className="w-2.5 h-2.5" />
                {tool}
              </span>
            ))}
          </div>
        )}

        {/* Message body */}
        <div
          className={cn(
            'rounded-lg px-3 py-2 text-sm cursor-pointer transition-all',
            'bg-surface-secondary dark:bg-[#1a1a35] border border-border dark:border-[#2a2a45]',
            message.message_type === 'challenge' && 'border-l-2 border-l-red-400',
            message.message_type === 'agreement' && 'border-l-2 border-l-green-400',
            message.message_type === 'evidence' && 'border-l-2 border-l-emerald-400',
            message.message_type === 'final_position' && 'border-l-2 border-l-indigo-400 bg-indigo-50/30 dark:bg-indigo-900/10',
          )}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <MarkdownRenderer content={message.content} />
            </div>
          ) : (
            <p className="text-text-secondary dark:text-gray-400 line-clamp-2">
              {message.content.slice(0, 200)}...
            </p>
          )}
        </div>

        {/* Confidence bar */}
        {message.confidence > 0 && message.message_type !== 'question' && (
          <div className="flex items-center gap-1.5 mt-1">
            <div className="h-1 flex-1 max-w-[60px] bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all',
                  message.confidence > 0.7 ? 'bg-green-500' : message.confidence > 0.4 ? 'bg-yellow-500' : 'bg-red-500',
                )}
                style={{ width: `${message.confidence * 100}%` }}
              />
            </div>
            <span className="text-[9px] text-text-muted dark:text-gray-600">
              {(message.confidence * 100).toFixed(0)}% confident
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Progress Bar ──────────────────────────────────────────────

function DebateProgressBar({ state }: { state: DebateChatState }) {
  const progress = state.maxTurns > 0 ? (state.totalTurns / state.maxTurns) * 100 : 0

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-border dark:border-[#2a2a45] bg-surface/50">
      <div className="flex items-center gap-1.5">
        <Users className="w-3.5 h-3.5 text-violet-500" />
        <span className="text-xs font-medium text-text-secondary dark:text-gray-400">
          {state.agents.length} agents
        </span>
      </div>
      <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-violet-500 to-fuchsia-500 rounded-full transition-all duration-500"
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>
      <span className="text-[10px] text-text-muted dark:text-gray-500">
        Turn {state.totalTurns}/{state.maxTurns}
      </span>
      {state.budgetRemaining < 0.3 && (
        <Badge variant="warning" size="sm">
          Budget {(state.budgetRemaining * 100).toFixed(0)}%
        </Badge>
      )}
      {!state.isActive && state.concludedBy && (
        <Badge variant="success" size="sm">
          Concluded: {state.concludedBy}
        </Badge>
      )}
    </div>
  )
}

// ── Agent List Sidebar ────────────────────────────────────────

function AgentSidebar({ agents }: { agents: DebateChatState['agents'] }) {
  return (
    <div className="w-[140px] shrink-0 border-r border-border dark:border-[#2a2a45] p-2 space-y-1.5 overflow-y-auto">
      <h4 className="text-[10px] font-semibold text-text-muted dark:text-gray-500 uppercase tracking-wider px-1 mb-2">
        Participants
      </h4>
      {agents.map((agent) => (
        <div
          key={agent.name}
          className={cn(
            'flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs',
            'hover:bg-surface-hover dark:hover:bg-white/[0.03]',
          )}
        >
          <AgentAvatar name={agent.name} role={agent.debate_role || agent.role} />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium text-text-primary dark:text-gray-200 truncate">
              {agent.name.split(' ').slice(0, 2).join(' ')}
            </p>
            <p className="text-[9px] text-text-muted dark:text-gray-500 capitalize truncate">
              {(agent.debate_role || agent.role).replace('_', ' ')}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────

export function DebateChatView({ state }: DebateChatViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Auto-scroll on new messages
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [state.messages.length, autoScroll])

  // Detect user scroll away from bottom
  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    const atBottom = scrollHeight - scrollTop - clientHeight < 60
    setAutoScroll(atBottom)
  }

  if (state.messages.length === 0) {
    return (
      <Card variant="default" padding="lg" className="text-center">
        <Users className="w-8 h-8 text-violet-400 mx-auto mb-2" />
        <p className="text-sm text-text-secondary dark:text-gray-400">
          Debate chat initializing...
        </p>
      </Card>
    )
  }

  return (
    <div className="flex flex-col h-full border border-border dark:border-[#2a2a45] rounded-lg overflow-hidden bg-surface dark:bg-[#0f0f25]">
      {/* Header with progress */}
      <DebateProgressBar state={state} />

      {/* Main content: sidebar + chat */}
      <div className="flex flex-1 min-h-0">
        {/* Agent sidebar */}
        <AgentSidebar agents={state.agents} />

        {/* Chat messages */}
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-4 py-3"
        >
          {state.messages.map((msg, idx) => {
            const prevMsg = idx > 0 ? state.messages[idx - 1] : null
            const isConsecutive = prevMsg?.agent_name === msg.agent_name && prevMsg?.message_type !== 'moderator'

            return (
              <ChatMessageBubble
                key={`${msg.turn}-${idx}`}
                message={msg}
                isConsecutive={isConsecutive}
              />
            )
          })}

          {/* Real-time activity indicator */}
          {state.isActive && state.currentSpeaker && (
            <div className="flex items-center gap-2 mt-3 px-3 py-2 rounded-lg bg-violet-50 dark:bg-violet-900/20 border border-violet-200 dark:border-violet-800/30">
              <div className="flex gap-1">
                {[0, 1, 2].map(i => (
                  <span
                    key={i}
                    className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-bounce"
                    style={{ animationDelay: `${i * 150}ms` }}
                  />
                ))}
              </div>
              <span className="text-xs text-violet-700 dark:text-violet-300 font-medium">
                {state.currentSpeaker}
              </span>
              <span className="text-xs text-text-muted dark:text-gray-400">
                {state.currentActivity === 'planning' && 'is planning their argument...'}
                {state.currentActivity === 'researching' && (
                  state.currentTool
                    ? `is researching — calling ${state.currentTool}...`
                    : 'is researching with tools...'
                )}
                {state.currentActivity === 'acting' && (
                  state.currentTool
                    ? `is executing — ${state.currentTool}...`
                    : 'is executing actions...'
                )}
                {state.currentActivity === 'arguing' && 'is composing their message...'}
                {!state.currentActivity && 'is working...'}
              </span>
            </div>
          )}
          {/* Fallback: generic indicator when active but no speaker info */}
          {state.isActive && !state.currentSpeaker && (
            <div className="flex items-center gap-2 mt-3 text-xs text-text-muted dark:text-gray-500">
              <div className="flex gap-1">
                {[0, 1, 2].map(i => (
                  <span
                    key={i}
                    className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-bounce"
                    style={{ animationDelay: `${i * 150}ms` }}
                  />
                ))}
              </div>
              <span>Debate in progress...</span>
            </div>
          )}
        </div>
      </div>

      {/* Scroll-to-bottom button */}
      {!autoScroll && (
        <button
          onClick={() => {
            setAutoScroll(true)
            scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
          }}
          className={cn(
            'absolute bottom-4 right-4 p-2 rounded-full shadow-lg',
            'bg-violet-500 text-white hover:bg-violet-600 transition-colors',
          )}
        >
          <ChevronDown className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}
