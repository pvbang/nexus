// ============================================================
// MekongAI Nexus - DebateCard
// Inline chat card showing a debate round with proposals/verdict
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Swords, ChevronDown, ChevronUp, CheckCircle2 } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { getAgentColor, getAgentEmoji } from '../../../lib/constants'
import { Badge } from '../../ui/Badge'
import { ProgressBar } from '../../ui/ProgressBar'
import type { DebateRound } from '../../../types/process'

interface DebateCardProps {
  round: DebateRound
}


export function DebateCard({ round }: DebateCardProps) {
  const shouldCollapse = round.proposals.length > 3
  const [expanded, setExpanded] = useState(!shouldCollapse)

  const visibleProposals = expanded
    ? round.proposals
    : round.proposals.slice(0, 3)

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border border-border bg-white px-3.5 py-2.5 text-sm',
        'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <Swords className="h-4 w-4 text-orange-500 dark:text-orange-400 shrink-0" />
        <span className="font-semibold text-text-primary dark:text-gray-100">
          Debate Round {round.round}
        </span>
        <Badge variant="default" size="sm">
          {round.proposals.length} proposals
        </Badge>
      </div>

      {/* Proposals */}
      <div className="space-y-2">
        {visibleProposals.map((proposal, idx) => {
          const colors = getAgentColor(proposal.agentRole, proposal.agentName)
          const emoji = getAgentEmoji(proposal.agentRole)
          return (
            <div
              key={`${proposal.agentId}-${idx}`}
              className={cn(
                'border-l-[3px] pl-3 py-1.5 text-xs',
                colors.border,
              )}
            >
              <div className="flex items-center gap-1.5 mb-0.5">
                <span className="text-[13px] shrink-0">{emoji}</span>
                <span className={cn('h-2 w-2 rounded-full shrink-0', colors.dot)} />
                <span className={cn('font-bold', colors.text)}>
                  {proposal.agentName}
                </span>
                {proposal.agentRole && (
                  <span className={cn('text-[9px] px-1.5 py-0.5 rounded-full font-semibold', colors.bg, colors.text)}>
                    {proposal.agentRole}
                  </span>
                )}
                {proposal.confidence != null && (
                  <Badge variant="info" size="sm" className="ml-auto">
                    {Math.round(proposal.confidence * 100)}%
                  </Badge>
                )}
              </div>
              <p className="text-text-secondary dark:text-gray-400 leading-relaxed line-clamp-3">
                {proposal.content}
              </p>
            </div>
          )
        })}
      </div>

      {/* Expand/collapse for >3 proposals */}
      {shouldCollapse && (
        <button
          type="button"
          onClick={() => setExpanded((p) => !p)}
          className="mt-1.5 flex items-center gap-1 text-xs text-brand-600 dark:text-brand-400 hover:underline"
        >
          {expanded ? (
            <>
              <ChevronUp className="h-3 w-3" />
              Show less
            </>
          ) : (
            <>
              <ChevronDown className="h-3 w-3" />
              Show all {round.proposals.length} proposals
            </>
          )}
        </button>
      )}

      {/* Verdict */}
      {round.verdict && (
        <div
          className={cn(
            'mt-2.5 rounded-md px-3 py-2',
            'bg-emerald-50 border border-emerald-200',
            'dark:bg-emerald-900/20 dark:border-emerald-800',
          )}
        >
          <div className="flex items-center gap-1.5 mb-1">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400" />
            <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400">
              Verdict{round.verdict.winner ? `: ${round.verdict.winner} selected` : ''}
            </span>
          </div>
          <p className="text-xs text-emerald-600 dark:text-emerald-300 leading-relaxed">
            {round.verdict.summary}
          </p>
          {round.verdict.confidence != null && (
            <div className="mt-1.5">
              <ProgressBar
                value={round.verdict.confidence * 100}
                variant="success"
                size="sm"
                showValue
              />
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}
