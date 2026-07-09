// ============================================================
// MekongAI Nexus - DebateView
// Displays swarm debate rounds with proposals, rebuttals, verdict
// ============================================================

import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Swords,
  ChevronDown,
  Trophy,
  MessageSquareQuote,
  ArrowRight,
  Shield,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { getAgentColor, getAgentEmoji } from '../../lib/constants'
import { useProcessStore } from '../../stores/processStore'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { ProgressBar } from '../ui/ProgressBar'
import { EmptyState } from '../ui/EmptyState'
import type { DebateRound } from '../../types/process'

// ── Single round component ────────────────────────────────────

function RoundCard({
  round,
  defaultOpen,
}: {
  round: DebateRound
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const hasVerdict = !!round.verdict

  return (
    <Card variant="default" padding="none" className="overflow-hidden">
      {/* Round header */}
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        className={cn(
          'flex w-full items-center justify-between gap-3 px-4 py-3 text-left',
          'hover:bg-gray-50/50 dark:hover:bg-white/[0.02] transition-colors',
        )}
      >
        <div className="flex items-center gap-2">
          <Swords className="h-4 w-4 text-brand-500 dark:text-brand-400" aria-hidden />
          <span className="text-sm font-semibold text-text-primary dark:text-gray-100">
            Round {round.round}
          </span>
          <Badge
            variant={hasVerdict ? 'success' : 'info'}
            size="sm"
            dot={!hasVerdict}
          >
            {hasVerdict ? 'Resolved' : 'In progress'}
          </Badge>
        </div>
        <ChevronDown
          className={cn(
            'h-4 w-4 text-text-muted dark:text-gray-500 transition-transform duration-200',
            open && 'rotate-180',
          )}
          aria-hidden
        />
      </button>

      {/* Content */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="space-y-4 px-4 pb-4 border-t border-border dark:border-[#2a2a45]">
              {/* Proposals */}
              <div className="pt-3">
                <h4 className="flex items-center gap-1.5 text-xs font-semibold text-text-secondary dark:text-gray-400 mb-2">
                  <MessageSquareQuote className="h-3.5 w-3.5" aria-hidden />
                  Proposals ({round.proposals.length})
                </h4>
                <div className="space-y-2">
                  {round.proposals.map((proposal, idx) => {
                    const colors = getAgentColor(proposal.agentRole, proposal.agentName)
                    const emoji = getAgentEmoji(proposal.agentRole)
                    return (
                      <div
                        key={`${proposal.agentId}-${idx}`}
                        className={cn(
                          'rounded-lg border-l-[3px] border p-3',
                          'border-border bg-gray-50/50',
                          'dark:border-[#2a2a45] dark:bg-[#12122a]',
                          colors.border,
                        )}
                      >
                        <div className="flex items-center justify-between mb-1.5">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px]">{emoji}</span>
                            <span className={cn('text-xs font-bold', colors.text)}>
                              {proposal.agentName}
                            </span>
                            <span className={cn('text-[9px] px-1.5 py-0.5 rounded-full font-semibold', colors.bg, colors.text)}>
                              {proposal.agentRole}
                            </span>
                          </div>
                          {proposal.confidence != null && (
                            <Badge variant="default" size="sm">
                              {Math.round(proposal.confidence * 100)}%
                            </Badge>
                          )}
                        </div>
                        <p className="text-sm text-text-primary dark:text-gray-200 leading-relaxed whitespace-pre-wrap">
                          {proposal.content}
                        </p>
                        {proposal.confidence != null && (
                          <div className="mt-2">
                            <ProgressBar
                              value={proposal.confidence * 100}
                              size="sm"
                              variant={
                                proposal.confidence >= 0.7
                                  ? 'success'
                                  : proposal.confidence >= 0.4
                                    ? 'warning'
                                    : 'error'
                              }
                            />
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Rebuttals */}
              {round.rebuttals && round.rebuttals.length > 0 && (
                <div>
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold text-text-secondary dark:text-gray-400 mb-2">
                    <Shield className="h-3.5 w-3.5" aria-hidden />
                    Rebuttals ({round.rebuttals.length})
                  </h4>
                  <div className="space-y-2 pl-4 border-l-2 border-amber-200 dark:border-amber-800">
                    {round.rebuttals.map((rebuttal, idx) => {
                      const rebuttalColors = getAgentColor(undefined, rebuttal.agentName)
                      return (
                        <div
                          key={`${rebuttal.agentId}-${idx}`}
                          className="text-sm"
                        >
                          <div className="flex items-center gap-1.5 mb-0.5">
                            <span className={cn('text-xs font-bold', rebuttalColors.text)}>
                              {rebuttal.agentName}
                            </span>
                            <ArrowRight className="h-3 w-3 text-text-muted dark:text-gray-500" aria-hidden />
                            <span className="text-xs text-text-muted dark:text-gray-500">
                              @{rebuttal.targetAgentId}
                            </span>
                          </div>
                          <p className="text-text-secondary dark:text-gray-400 leading-relaxed whitespace-pre-wrap">
                            {rebuttal.content}
                          </p>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Verdict */}
              {round.verdict && (
                <div
                  className={cn(
                    'rounded-lg border-2 p-3',
                    'border-emerald-200 bg-emerald-50/50',
                    'dark:border-emerald-800 dark:bg-emerald-900/20',
                  )}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <Trophy className="h-4 w-4 text-emerald-600 dark:text-emerald-400" aria-hidden />
                    <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400">
                      Verdict
                    </span>
                    {round.verdict.winner && (
                      <Badge variant="success" size="sm">
                        Winner: {round.verdict.winner}
                      </Badge>
                    )}
                    {round.verdict.confidence != null && (
                      <Badge variant="default" size="sm">
                        {Math.round(round.verdict.confidence * 100)}% confidence
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm text-text-primary dark:text-gray-200 leading-relaxed">
                    {round.verdict.summary}
                  </p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  )
}

// ── Main component ────────────────────────────────────────────

export function DebateView() {
  const debateRounds = useProcessStore((s) => s.debateRounds)

  if (debateRounds.length === 0) {
    return (
      <EmptyState
        icon={<Swords className="h-10 w-10" />}
        title="No debates yet"
        description="Debate rounds will appear here when agents discuss approaches."
      />
    )
  }

  return (
    <div className="space-y-3 animate-fade-in">
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted dark:text-gray-500">
          {debateRounds.length} round{debateRounds.length !== 1 ? 's' : ''}
        </span>
      </div>
      {debateRounds.map((round, idx) => (
        <RoundCard
          key={round.round}
          round={round}
          // Latest round open by default, older ones collapsed
          defaultOpen={idx === debateRounds.length - 1}
        />
      ))}
    </div>
  )
}
