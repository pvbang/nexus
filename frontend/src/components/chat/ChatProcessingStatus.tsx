// ============================================================
// MekongAI Nexus - ChatProcessingStatus
// Displays processing status, progress steps, agent activities,
// live debate, and streaming content during AI response.
// Extracted from ChatPanel render section for performance isolation.
// React.memo prevents re-render from unrelated state changes.
// ============================================================

import React from 'react'
import { RefreshCw, PlayCircle } from 'lucide-react'
import { ProcessStream } from '../process/ProcessStream'
import { LiveDebateStream, type LiveDebateState } from './LiveDebateStream'
import { InlineDebateCard } from './InlineDebateCard'
import { InlineDebateChatStream } from './InlineDebateChatStream'
import { DecisionPrompt } from './DecisionPrompt'
import { ProcessingIndicator, type StepInfo, type AgentActivity } from './ProcessingIndicator'
import type { PendingDecision } from '../../api'
import type { SwarmRoundEvent } from '../../types/execution'

// ── Props ─────────────────────────────────────────────────────

export interface ChatProcessingStatusProps {
  sessionId: string
  isProcessing: boolean
  processStageActive: boolean
  executionActive: boolean
  progressSteps: StepInfo[]
  agentActivities: AgentActivity[]
  statusText: string
  liveDebate: LiveDebateState | null
  inlineDebateRounds: SwarmRoundEvent[]
  pendingDecision: PendingDecision | null
  onDecisionResolved: () => void
  canRetry: boolean
  onRetry: () => void
  canResume: boolean
  onResume: () => void
  resumeInfo?: { turn: number; taskId: string } | null
}

// ── Component ─────────────────────────────────────────────────

export const ChatProcessingStatus = React.memo(function ChatProcessingStatus({
  sessionId,
  isProcessing,
  processStageActive,
  executionActive,
  progressSteps,
  agentActivities,
  statusText,
  liveDebate,
  inlineDebateRounds,
  pendingDecision,
  onDecisionResolved,
  canRetry,
  onRetry,
  canResume,
  onResume,
  resumeInfo,
}: ChatProcessingStatusProps) {
  return (
    <>
      {/* Live ProcessStream — shows when task is active (SSE streaming OR background execution) */}
      {(isProcessing || processStageActive) && (
        <ProcessStream sessionId={sessionId} compact={false} />
      )}

      {/* Live debate stream — realtime proposals/rebuttals/verdicts as they arrive */}
      {liveDebate && liveDebate.rounds.length > 0 && (
        <LiveDebateStream state={liveDebate} />
      )}

      {/* Legacy inline debate rounds (backward compat — only when no live debate) */}
      {(!liveDebate || liveDebate.rounds.length === 0) &&
        inlineDebateRounds
          .filter((round) => round.proposals?.length > 0)
          .map((round) => <InlineDebateCard key={round.id} round={round} />)}

      {/* DEBATE-UI-FIX: Chat-based debate stream */}
      <InlineDebateChatStream sessionId={sessionId} />

      {/* Interactive Decision — agent needs user input */}
      {pendingDecision && (
        <DecisionPrompt
          decision={pendingDecision}
          onResolved={onDecisionResolved}
        />
      )}

      {/* Processing indicator */}
      {(progressSteps.length > 0 ||
        agentActivities.length > 0 ||
        isProcessing ||
        processStageActive) && (
        <ProcessingIndicator
          progressSteps={progressSteps}
          agentActivities={agentActivities}
          statusText={
            isProcessing || processStageActive || executionActive
              ? statusText
              : undefined
          }
          isComplete={!isProcessing && !processStageActive && !executionActive}
        />
      )}

      {/* Resume button — shown when task crashed/failed with checkpoint */}
      {canResume && !isProcessing && (
        <div className="flex justify-center mt-2">
          <button
            onClick={onResume}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-green-600 hover:bg-green-700 dark:bg-green-700 dark:hover:bg-green-600 rounded-lg shadow-sm transition-colors"
          >
            <PlayCircle className="w-4 h-4" />
            Tiếp tục nhiệm vụ
            {resumeInfo && (
              <span className="text-green-200 text-xs ml-1">
                (từ turn {resumeInfo.turn})
              </span>
            )}
          </button>
        </div>
      )}

      {/* UI-03: Retry button after error */}
      {canRetry && !isProcessing && !canResume && (
        <div className="flex justify-center">
          <button
            onClick={onRetry}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            Retry last message
          </button>
        </div>
      )}
    </>
  )
})
