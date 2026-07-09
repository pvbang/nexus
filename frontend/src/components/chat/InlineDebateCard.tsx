// ============================================================
// MekongAI Nexus - InlineDebateCard
// Debate round visualization — 2-column layout:
//   Left: Agent name buttons (narrow)
//   Right: Content of selected agent (expandable)
// ============================================================

import { useState } from 'react'
import { Users, ChevronDown, ChevronRight, Wrench, Trophy, Gavel } from 'lucide-react'
import { cn } from '../../lib/cn'
import { getAgentColor, getAgentEmoji } from '../../lib/constants'
import { sanitizeDebateContent } from '../../lib/utils'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import type { SwarmRoundEvent } from '../../types/execution'

// ── Props ─────────────────────────────────────────────────────

export interface InlineDebateCardProps {
  round: SwarmRoundEvent
  defaultExpanded?: boolean
}

// ── Component ─────────────────────────────────────────────────

export function InlineDebateCard({ round, defaultExpanded = true }: InlineDebateCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [selectedAgent, setSelectedAgent] = useState<number>(0) // index into proposals

  // Collect all agents from proposals
  const agents = round.proposals.map((p, idx) => ({
    idx,
    name: p.agent,
    role: p.role,
    content: p.content,
    tools_used: p.tools_used,
  }))

  // Find rebuttal for selected agent
  const selectedRebuttal = round.rebuttals?.find(
    r => r.agent === agents[selectedAgent]?.name
  )

  return (
    <div className="flex justify-start animate-slide-up">
      <div className="max-w-[90%] sm:max-w-[85%] w-full">
        {/* Round header */}
        <div className="flex items-center gap-2 mb-1.5">
          <div className="w-6 h-6 rounded-[7px] bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center">
            <Users className="w-3 h-3 text-white" />
          </div>
          <span className="text-[11px] font-semibold text-text-muted dark:text-gray-500 uppercase tracking-wider">
            Multi-Agent Debate
          </span>
          {round.round > 0 && (
            <span className="text-[10px] text-violet-500 dark:text-violet-400 font-medium">
              Round {round.round}
            </span>
          )}
        </div>

        {/* Card container */}
        <div
          className={cn(
            'rounded-[14px] rounded-bl-[4px] overflow-hidden border',
            'bg-gradient-to-r from-violet-50 to-fuchsia-50 border-violet-200',
            'dark:from-violet-900/20 dark:to-fuchsia-900/20 dark:border-violet-800',
          )}
        >
          {/* Collapse toggle header */}
          <button
            onClick={() => setExpanded(!expanded)}
            className={cn(
              'w-full flex items-center gap-2 px-4 py-2.5 text-left transition-colors',
              'hover:bg-violet-100/30 dark:hover:bg-violet-800/20',
            )}
          >
            <span className="text-[13px] font-bold text-violet-800 dark:text-violet-300 flex-1">
              🗣️ {agents.length} agents debating
              {round.rebuttals && round.rebuttals.length > 0 && ` · ${round.rebuttals.length} rebuttals`}
            </span>
            {round.verdict?.verdict === 'resolved' && (
              <span className="px-2 py-0.5 bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400 text-[10px] font-semibold rounded-full">
                ✅ Resolved
              </span>
            )}
            {expanded
              ? <ChevronDown className="w-4 h-4 text-violet-400 shrink-0" />
              : <ChevronRight className="w-4 h-4 text-violet-400 shrink-0" />}
          </button>

          {/* Expandable 2-column layout */}
          {expanded && (
            <div className="border-t border-violet-200/50 dark:border-violet-700/30">
              <div className="flex min-h-[200px]">
                {/* ── Left: Agent buttons ── */}
                <div className="w-[200px] shrink-0 border-r border-violet-200/50 dark:border-violet-700/30 bg-violet-50/50 dark:bg-violet-900/10 p-2 space-y-1.5 overflow-y-auto">
                  {agents.map((agent, idx) => {
                    const emoji = getAgentEmoji(agent.role)
                    const isSelected = selectedAgent === idx
                    return (
                      <button
                        key={`${agent.name}-${idx}`}
                        onClick={() => setSelectedAgent(idx)}
                        className={cn(
                          'w-full text-left px-2.5 py-2 rounded-lg transition-all text-[11px] font-medium',
                          'flex items-center gap-1.5',
                          isSelected
                            ? 'bg-white dark:bg-violet-800/40 shadow-sm border border-violet-300 dark:border-violet-600'
                            : 'hover:bg-white/60 dark:hover:bg-violet-800/20 border border-transparent',
                        )}
                      >
                        <span className="text-[14px]">{emoji}</span>
                        <span className={cn(
                          'break-words leading-tight',
                          isSelected ? 'text-violet-800 dark:text-violet-200' : 'text-gray-600 dark:text-gray-400',
                        )}>
                          {agent.name}
                        </span>
                      </button>
                    )
                  })}

                  {/* Verdict button */}
                  {round.verdict && (
                    <button
                      onClick={() => setSelectedAgent(-1)}
                      className={cn(
                        'w-full text-left px-2.5 py-2 rounded-lg transition-all text-[11px] font-medium',
                        'flex items-center gap-1.5 mt-2 border-t border-violet-200/50 dark:border-violet-700/30 pt-2',
                        selectedAgent === -1
                          ? 'bg-emerald-50 dark:bg-emerald-900/30 shadow-sm border border-emerald-300 dark:border-emerald-700'
                          : 'hover:bg-emerald-50/60 dark:hover:bg-emerald-900/20 border border-transparent',
                      )}
                    >
                      <Gavel className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                      <span className={cn(
                        'break-words leading-tight',
                        selectedAgent === -1 ? 'text-emerald-700 dark:text-emerald-300' : 'text-gray-600 dark:text-gray-400',
                      )}>
                        Verdict
                      </span>
                    </button>
                  )}
                </div>

                {/* ── Right: Content panel ── */}
                <div className="flex-1 p-4 overflow-y-auto max-h-[70vh]">
                  {selectedAgent >= 0 && agents[selectedAgent] ? (
                    <AgentContent
                      agent={agents[selectedAgent]}
                      rebuttal={selectedRebuttal}
                    />
                  ) : round.verdict ? (
                    <VerdictContent verdict={round.verdict} />
                  ) : (
                    <div className="text-[13px] text-gray-400 italic">Select an agent to view their proposal</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────

function AgentContent({ agent, rebuttal }: {
  agent: { name: string; role?: string; content: string; tools_used?: string[] }
  rebuttal?: { agent: string; content: string }
}) {
  const colors = getAgentColor(agent.role, agent.name)
  const emoji = getAgentEmoji(agent.role)

  return (
    <div className="space-y-3">
      {/* Agent header */}
      <div className="flex items-center gap-2">
        <div className={cn('w-7 h-7 rounded-full flex items-center justify-center text-[15px]', colors.bg)}>
          {emoji}
        </div>
        <span className={cn('text-[14px] font-bold', colors.text)}>
          {agent.name}
        </span>
        {agent.role && (
          <span className={cn('text-[10px] px-2 py-0.5 rounded-full font-semibold', colors.bg, colors.text)}>
            {agent.role}
          </span>
        )}
        {agent.tools_used && agent.tools_used.length > 0 && (
          <span className="text-[9px] text-gray-400 dark:text-gray-500 flex items-center gap-0.5 ml-auto">
            <Wrench className="w-2.5 h-2.5" /> {agent.tools_used.length} tools
          </span>
        )}
      </div>

      {/* Proposal content */}
      <div className="text-[13px] text-gray-700 dark:text-gray-300 leading-relaxed prose prose-sm dark:prose-invert max-w-none">
        <MarkdownRenderer content={sanitizeDebateContent(agent.content) || '(Đang suy nghĩ...)'} />
      </div>

      {/* Rebuttal (if exists for this agent) */}
      {rebuttal && (
        <div className={cn(
          'rounded-lg border-l-[3px] border p-3 mt-3',
          'bg-amber-50/60 border-amber-300',
          'dark:bg-amber-900/20 dark:border-amber-700',
        )}>
          <div className="text-[11px] font-semibold text-amber-700 dark:text-amber-400 mb-1">
            ⚔️ Rebuttal from {rebuttal.agent}
          </div>
          <div className="text-[12px] text-gray-700 dark:text-gray-300 leading-relaxed">
            <MarkdownRenderer content={sanitizeDebateContent(rebuttal.content)} />
          </div>
        </div>
      )}
    </div>
  )
}

function VerdictContent({ verdict }: {
  verdict: { verdict?: string; winner?: string | null; reasoning?: string; synthesis?: string }
}) {
  return (
    <div className="space-y-3">
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
            ✨ Synthesis
          </span>
          <div className="text-[13px] text-gray-700 dark:text-gray-300 leading-relaxed">
            <MarkdownRenderer content={sanitizeDebateContent(verdict.synthesis)} />
          </div>
        </div>
      )}
    </div>
  )
}
