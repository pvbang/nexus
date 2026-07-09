// ============================================================
// MekongAI Nexus - LiveDebateStream
// Realtime debate visualization — 2-column split layout:
//   Left (narrow): Agent name buttons + Verdict button
//   Right: Content of selected agent (collapsible per round)
// Auto-updates as agents stream proposals/rebuttals/verdicts.
// ============================================================

import { useState, useEffect, useRef, useCallback } from 'react'
import { Users, ChevronDown, ChevronRight, Wrench, Gavel, Swords, Loader2, Trophy } from 'lucide-react'
import { cn } from '../../lib/cn'
import { getAgentColor, getAgentEmoji } from '../../lib/constants'
import { sanitizeDebateContent } from '../../lib/utils'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'

// ── Types ─────────────────────────────────────────────────────

export interface LiveProposal {
  agent_name: string
  agent_role: string
  content: string
  tools_used: string[]
}

export interface LiveRebuttal {
  agent_name: string
  content: string
}

export interface LiveVerdict {
  verdict?: string
  winner?: string | null
  synthesis?: string
  reasoning?: string
}

export interface LiveDebateRound {
  round: number
  total_rounds: number
  proposals: LiveProposal[]
  rebuttals: LiveRebuttal[]
  verdict: LiveVerdict | null
}

export interface LiveDebateState {
  rounds: LiveDebateRound[]
  currentRound: number
  totalRounds: number
  isComplete: boolean
}

// ── Props ─────────────────────────────────────────────────────

interface LiveDebateStreamProps {
  state: LiveDebateState
}


// ── Spinner ───────────────────────────────────────────────────

function PulsingDots() {
  return (
    <span className="inline-flex items-center gap-[3px] ml-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-[5px] h-[5px] rounded-full bg-violet-400 dark:bg-violet-500"
          style={{
            animation: 'typingBounce 0.6s ease-in-out infinite',
            animationDelay: `${i * 150}ms`,
          }}
        />
      ))}
    </span>
  )
}

// ── Agent button in left panel ────────────────────────────────

interface AgentButtonProps {
  name: string
  role?: string
  isSelected: boolean
  isStreaming?: boolean
  hasRebuttal?: boolean
  onClick: () => void
}

function AgentButton({ name, role, isSelected, isStreaming, hasRebuttal, onClick }: AgentButtonProps) {
  const emoji = getAgentEmoji(role)

  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full text-left px-2.5 py-2 rounded-lg transition-all text-[11px] font-medium',
        'flex items-center gap-1.5 relative',
        isSelected
          ? 'bg-white dark:bg-violet-800/40 shadow-sm border border-violet-300 dark:border-violet-600'
          : 'hover:bg-white/60 dark:hover:bg-violet-800/20 border border-transparent',
      )}
    >
      <span className="text-[14px] shrink-0">{emoji}</span>
      <span className={cn(
        'flex-1 break-words leading-tight',
        isSelected ? 'text-violet-800 dark:text-violet-200' : 'text-gray-600 dark:text-gray-400',
      )}>
        {name}
      </span>
      {/* Indicators */}
      <span className="flex items-center gap-0.5 shrink-0">
        {isStreaming && (
          <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
        )}
        {hasRebuttal && (
          <Swords className="w-2.5 h-2.5 text-amber-500" />
        )}
      </span>
    </button>
  )
}

// ── Right panel: Agent content ────────────────────────────────

function AgentContentPanel({ proposal, rebuttal }: {
  proposal: LiveProposal
  rebuttal?: LiveRebuttal
}) {
  const colors = getAgentColor(proposal.agent_role, proposal.agent_name)
  const emoji = getAgentEmoji(proposal.agent_role)

  return (
    <div className="space-y-3 animate-fade-in">
      {/* Agent header */}
      <div className="flex items-center gap-2">
        <div className={cn('w-7 h-7 rounded-full flex items-center justify-center text-[15px]', colors.bg)}>
          {emoji}
        </div>
        <span className={cn('text-[14px] font-bold', colors.text)}>
          {proposal.agent_name}
        </span>
        {proposal.agent_role && (
          <span className={cn('text-[10px] px-2 py-0.5 rounded-full font-semibold', colors.bg, colors.text)}>
            {proposal.agent_role}
          </span>
        )}
        {proposal.tools_used.length > 0 && (
          <span className="text-[9px] text-gray-400 dark:text-gray-500 flex items-center gap-0.5 ml-auto">
            <Wrench className="w-2.5 h-2.5" /> {proposal.tools_used.join(', ')}
          </span>
        )}
      </div>

      {/* Proposal content */}
      <div className="text-[13px] text-gray-700 dark:text-gray-300 leading-relaxed prose prose-sm dark:prose-invert max-w-none prose-headings:text-[14px] prose-headings:font-bold prose-headings:mt-3 prose-headings:mb-1 prose-p:my-1 prose-table:text-[12px] prose-td:px-2 prose-td:py-1 prose-th:px-2 prose-th:py-1 prose-table:border prose-td:border prose-th:border prose-th:bg-gray-100 dark:prose-th:bg-gray-800">
        <MarkdownRenderer content={sanitizeDebateContent(proposal.content) || '(Đang suy nghĩ...)'} />
      </div>

      {/* Rebuttal (if exists for this agent) */}
      {rebuttal && (
        <div className={cn(
          'rounded-lg border-l-[3px] border p-3 mt-3',
          'bg-amber-50/60 border-amber-300',
          'dark:bg-amber-900/20 dark:border-amber-700',
        )}>
          <div className="text-[11px] font-semibold text-amber-700 dark:text-amber-400 mb-1 flex items-center gap-1">
            <Swords className="w-3 h-3" />
            Phản biện từ {rebuttal.agent_name}
          </div>
          <div className="text-[12px] text-gray-700 dark:text-gray-300 leading-relaxed prose prose-sm dark:prose-invert max-w-none prose-p:my-1">
            <MarkdownRenderer content={sanitizeDebateContent(rebuttal.content)} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Right panel: Verdict content ──────────────────────────────

function VerdictPanel({ verdict }: { verdict: LiveVerdict }) {
  return (
    <div className="space-y-3 animate-fade-in">
      <div className="flex items-center gap-2">
        <Gavel className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
        <span className="text-[14px] font-bold text-gray-700 dark:text-gray-300">
          Judge Verdict: {(verdict.verdict || 'pending').toUpperCase()}
        </span>
        {verdict.winner && (
          <span className="px-2 py-0.5 bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400 text-[11px] font-medium rounded-full flex items-center gap-1">
            <Trophy className="w-3 h-3" /> {verdict.winner}
          </span>
        )}
      </div>

      {verdict.reasoning && (
        <div className="text-[13px] text-gray-600 dark:text-gray-400 leading-relaxed">
          <MarkdownRenderer content={sanitizeDebateContent(verdict.reasoning)} />
        </div>
      )}

      {verdict.synthesis && (
        <div className={cn(
          'rounded-lg p-3 border',
          'bg-emerald-50 border-emerald-200',
          'dark:bg-emerald-900/20 dark:border-emerald-800',
        )}>
          <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 block mb-1">
            ✨ Tổng hợp
          </span>
          <div className="text-[13px] text-gray-700 dark:text-gray-300 leading-relaxed prose prose-sm dark:prose-invert max-w-none prose-p:my-1">
            <MarkdownRenderer content={sanitizeDebateContent(verdict.synthesis)} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Round component (2-column layout) ─────────────────────────

function DebateRoundCard({ round, isLatest }: { round: LiveDebateRound; isLatest: boolean }) {
  const [expanded, setExpanded] = useState(true)
  // Auto-select latest agent (last proposal added)
  const [selectedAgent, setSelectedAgent] = useState<number>(() =>
    round.proposals.length > 0 ? round.proposals.length - 1 : 0
  )
  // -1 means verdict is selected
  const [showVerdict, setShowVerdict] = useState(false)

  const hasProposals = round.proposals.length > 0
  const hasRebuttals = round.rebuttals.length > 0
  const hasVerdict = round.verdict !== null
  const isResolved = round.verdict?.verdict === 'resolved'

  // Auto-select new agent when proposals arrive
  const prevProposalCount = useRef(round.proposals.length)
  useEffect(() => {
    if (round.proposals.length > prevProposalCount.current) {
      setSelectedAgent(round.proposals.length - 1)
      setShowVerdict(false)
    }
    prevProposalCount.current = round.proposals.length
  }, [round.proposals.length])

  // Auto-show verdict when it arrives
  useEffect(() => {
    if (hasVerdict && !showVerdict) {
      setShowVerdict(true)
      setSelectedAgent(-1)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasVerdict])

  // Find rebuttal targeting the selected agent
  const selectedRebuttal = selectedAgent >= 0 && round.proposals[selectedAgent]
    ? round.rebuttals.find(r => r.agent_name !== round.proposals[selectedAgent].agent_name)
      || round.rebuttals.find(r => r.agent_name === round.proposals[selectedAgent].agent_name)
    : undefined

  const handleSelectAgent = useCallback((idx: number) => {
    setSelectedAgent(idx)
    setShowVerdict(false)
  }, [])

  const handleSelectVerdict = useCallback(() => {
    setSelectedAgent(-1)
    setShowVerdict(true)
  }, [])

  return (
    <div
      className={cn(
        'rounded-[14px] overflow-hidden border transition-all duration-300',
        isLatest
          ? 'border-violet-300 dark:border-violet-700 shadow-[0_2px_12px_rgba(139,92,246,0.12)]'
          : 'border-gray-200 dark:border-gray-700/50 opacity-90',
      )}
    >
      {/* Round collapse header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className={cn(
          'w-full flex items-center gap-2.5 px-4 py-2.5 text-left transition-colors',
          'bg-gradient-to-r from-violet-50/80 to-fuchsia-50/60',
          'dark:from-violet-900/25 dark:to-fuchsia-900/15',
          'hover:from-violet-100/80 hover:to-fuchsia-100/60',
          'dark:hover:from-violet-900/35 dark:hover:to-fuchsia-900/25',
        )}
      >
        <div className="w-6 h-6 rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center shrink-0">
          <span className="text-[11px] font-bold text-white">{round.round}</span>
        </div>
        <span className="text-[13px] font-bold text-violet-800 dark:text-violet-300 flex-1">
          Round {round.round}/{round.total_rounds}
        </span>
        <div className="flex items-center gap-1.5">
          {hasProposals && (
            <span className="px-1.5 py-0.5 text-[9px] font-medium rounded-full bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-400">
              {round.proposals.length} agents
            </span>
          )}
          {hasRebuttals && (
            <span className="px-1.5 py-0.5 text-[9px] font-medium rounded-full bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400">
              {round.rebuttals.length} phản biện
            </span>
          )}
          {isResolved && (
            <span className="px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
              ✅ Resolved
            </span>
          )}
          {hasVerdict && !isResolved && (
            <span className="px-1.5 py-0.5 text-[9px] font-medium rounded-full bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
              🔄 Tiếp tục
            </span>
          )}
          {isLatest && !hasVerdict && (
            <Loader2 className="w-3.5 h-3.5 text-violet-500 animate-spin" />
          )}
        </div>
        {expanded
          ? <ChevronDown className="w-4 h-4 text-violet-400 shrink-0" />
          : <ChevronRight className="w-4 h-4 text-violet-400 shrink-0" />}
      </button>

      {/* Expandable 2-column layout */}
      {expanded && (
        <div className="border-t border-violet-200/50 dark:border-violet-700/30">
          <div className="flex min-h-[220px]">
            {/* ── Left: Agent buttons sidebar ── */}
            <div className="w-[200px] shrink-0 border-r border-violet-200/50 dark:border-violet-700/30 bg-violet-50/50 dark:bg-violet-900/10 p-2 space-y-1.5 overflow-y-auto max-h-[70vh]">
              {round.proposals.map((p, idx) => {
                const agentRebuttal = round.rebuttals.find(r => r.agent_name === p.agent_name)
                return (
                  <AgentButton
                    key={`${round.round}-${p.agent_name}-${p.agent_role}`}
                    name={p.agent_name}
                    role={p.agent_role}
                    isSelected={selectedAgent === idx && !showVerdict}
                    isStreaming={isLatest && idx === round.proposals.length - 1 && !hasVerdict}
                    hasRebuttal={!!agentRebuttal}
                    onClick={() => handleSelectAgent(idx)}
                  />
                )
              })}

              {/* Loading indicator for more agents */}
              {isLatest && !hasVerdict && (
                <div className="flex items-center gap-1.5 px-2.5 py-2 text-[10px] text-violet-500 dark:text-violet-400 italic">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span>Đang chờ...</span>
                  <PulsingDots />
                </div>
              )}

              {/* Verdict button */}
              {hasVerdict && (
                <button
                  onClick={handleSelectVerdict}
                  className={cn(
                    'w-full text-left px-2.5 py-2 rounded-lg transition-all text-[11px] font-medium',
                    'flex items-center gap-1.5 mt-2 border-t border-violet-200/50 dark:border-violet-700/30 pt-2',
                    showVerdict
                      ? 'bg-emerald-50 dark:bg-emerald-900/30 shadow-sm border border-emerald-300 dark:border-emerald-700'
                      : 'hover:bg-emerald-50/60 dark:hover:bg-emerald-900/20 border border-transparent',
                  )}
                >
                  <Gavel className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
                  <span className={cn(
                    'break-words leading-tight',
                    showVerdict ? 'text-emerald-700 dark:text-emerald-300' : 'text-gray-600 dark:text-gray-400',
                  )}>
                    Verdict
                  </span>
                  {isResolved && <span className="text-[10px] ml-auto">✅</span>}
                </button>
              )}
            </div>

            {/* ── Right: Content panel ── */}
            <div className="flex-1 p-4 overflow-y-auto max-h-[70vh] bg-white/50 dark:bg-[#0f0f1e]/50">
              {showVerdict && round.verdict ? (
                <VerdictPanel verdict={round.verdict} />
              ) : selectedAgent >= 0 && round.proposals[selectedAgent] ? (
                <AgentContentPanel
                  proposal={round.proposals[selectedAgent]}
                  rebuttal={selectedRebuttal}
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center gap-2 py-8">
                  <Users className="w-8 h-8 text-violet-300 dark:text-violet-700" />
                  <span className="text-[13px] text-gray-400 dark:text-gray-600 italic">
                    Chọn một agent ở bên trái để xem nội dung
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────

export function LiveDebateStream({ state }: LiveDebateStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new content arrives
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [state.rounds])

  if (state.rounds.length === 0) return null

  return (
    <div className="flex justify-start animate-slide-up">
      <div className="max-w-[92%] sm:max-w-[88%] w-full">
        {/* Header */}
        <div className="flex items-center gap-2 mb-2">
          <div className="w-6 h-6 rounded-[7px] bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center">
            <Users className="w-3 h-3 text-white" />
          </div>
          <span className="text-[11px] font-semibold text-text-muted dark:text-gray-500 uppercase tracking-wider">
            Multi-Agent Debate
          </span>
          {!state.isComplete && (
            <span className="flex items-center gap-1 text-[10px] text-violet-500 dark:text-violet-400 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
              LIVE
            </span>
          )}
          {state.isComplete && (
            <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium">
              ✅ Hoàn tất
            </span>
          )}
        </div>

        {/* Rounds */}
        <div className="space-y-3" ref={scrollRef}>
          {state.rounds.map((round, idx) => (
            <DebateRoundCard
              key={`live-round-${round.round}`}
              round={round}
              isLatest={idx === state.rounds.length - 1 && !state.isComplete}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
