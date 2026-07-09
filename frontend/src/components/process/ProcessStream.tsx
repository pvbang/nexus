// ============================================================
// MekongAI Nexus - ProcessStream
// Renders process events chronologically inline within chat
// ============================================================

import { useState, useMemo, useCallback, useRef, useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Eye, EyeOff, CheckCircle2, Timer, StopCircle } from 'lucide-react'
import { cn } from '../../lib/cn'
import { apiPost } from '../../api'
import { useProcessStore } from '../../stores/processStore'
import { useExecutionStore } from '../../stores/executionStore'
import { useRealtimeStore } from '../../stores/realtimeStore'
import { usePreferenceStore } from '../../stores/preferenceStore'
import type { ExecutionEvent } from '../../types/execution'
import { Badge } from '../ui/Badge'
import { TimelineCard } from './TimelineCard'
import {
  convertExecutionEventsToTimeline,
  buildSupplementalTimeline,
} from './timeline-utils'
import type { TimelineItem } from './timeline-utils'
import type {
  ProcessPlan,
  AgentMessage,
  AgentInfo,
  ToolExecution,
  DebateRound,
  SynthesisEvent,
  XSkillEvent,
  ToolCreationEvent,
  DeepAnalysisResult,
  SelfReflectionResult,
  AutoVerifyResult,
  CompletionCheckResult,
  MidAnalysisResult,
  CritiqueResult,
  RevisionResult,
} from '../../types/process'

// —— Timeline item types imported from ./timeline-utils ————————
// (TimelineItem, convertExecutionEventsToTimeline, formatEventTime)

// —— Props ——————————————————————————————————————————

interface ProcessStreamProps {
  sessionId: string
  compact?: boolean
}

// —— Helpers ————————————————————————————————————————

function buildTimeline(
  plan: ProcessPlan | null,
  agentMessages: AgentMessage[],
  trackedAgents: AgentInfo[],
  toolExecutions: ToolExecution[],
  debateRounds: DebateRound[],
  synthesisEvents: SynthesisEvent[],
  xskillEvents: XSkillEvent[],
  toolCreations: ToolCreationEvent[],
  agentThinkingEvents: Array<{ agentName: string; agentRole: string; content: string; timestamp: number }>,
  isActive: boolean,
  // Deep reasoning data
  deepAnalysis: DeepAnalysisResult | null,
  selfReflections: SelfReflectionResult[],
  verifications: AutoVerifyResult[],
  completionCheck: CompletionCheckResult | null,
  midAnalyses: MidAnalysisResult[],
  critiques: CritiqueResult[],
  revisions: RevisionResult[],
  _reactPhase?: string,
): TimelineItem[] {
  // DEFENSIVE: Wrap entire buildTimeline in try/catch — return empty on catastrophic failure
  try {
  const items: TimelineItem[] = []

  // Plan
  if (plan) {
    items.push({ kind: 'plan', timestamp: 0, data: plan })
  }

  // Deep Analysis — right after planning
  if (deepAnalysis) {
    const ts = deepAnalysis.timestamp ? new Date(deepAnalysis.timestamp).getTime() : 0.5
    items.push({ kind: 'deep_analysis', timestamp: ts, data: deepAnalysis })
  }

  // React Phase indicator — REMOVED: spam noise in timeline, not persisted

  // Agent spawns — prefer tracked agents from processStore (populated by agent_spawn events)
  // Fall back to agents extracted from agentMessages for backward compatibility
  if (trackedAgents.length > 0) {
    const agents = trackedAgents.map((a) => ({
      id: a.id,
      name: a.name,
      role: a.role,
      tools: a.toolsUsed ?? [],
    }))
    items.push({ kind: 'agent_spawn', timestamp: 1, data: agents })
  } else if (agentMessages.length > 0) {
    const agentMap = new Map<string, { id: string; name: string; role: string; tools: string[] }>()
    for (const msg of agentMessages) {
      if (!agentMap.has(msg.fromAgent.id)) {
        agentMap.set(msg.fromAgent.id, {
          id: msg.fromAgent.id,
          name: msg.fromAgent.name,
          role: msg.fromAgent.role,
          tools: [],
        })
      }
    }
    const agents = Array.from(agentMap.values())
    if (agents.length > 0) {
      items.push({ kind: 'agent_spawn', timestamp: 1, data: agents })
    }
  }

  // Agent communication messages
  for (const msg of agentMessages) {
    items.push({ kind: 'agent_communication', timestamp: msg.timestamp, data: msg })
  }

  // Agent thinking events
  for (const evt of agentThinkingEvents) {
    const isLast = evt === agentThinkingEvents[agentThinkingEvents.length - 1]
    items.push({
      kind: 'agent_thinking',
      timestamp: evt.timestamp,
      data: {
        agentName: evt.agentName,
        agentRole: evt.agentRole,
        content: evt.content,
        isActive: isActive && isLast,
      },
    })
  }

  // Tool executions
  for (const exec of toolExecutions) {
    items.push({ kind: 'tool_call', timestamp: exec.timestamp, data: exec })
  }

  // Auto-verifications (after tool executions)
  for (const verify of verifications) {
    const ts = verify.timestamp ? new Date(verify.timestamp).getTime() : Date.now()
    items.push({ kind: 'auto_verify', timestamp: ts, data: verify })
  }

  // Mid-analyses
  for (const mid of midAnalyses) {
    const ts = mid.timestamp ? new Date(mid.timestamp).getTime() : Date.now()
    items.push({ kind: 'mid_analysis', timestamp: ts, data: mid })
  }

  // Self-reflections
  for (const reflection of selfReflections) {
    const ts = reflection.timestamp ? new Date(reflection.timestamp).getTime() : Date.now()
    items.push({ kind: 'self_reflection', timestamp: ts, data: reflection })
  }

  // Debate rounds (skip empty rounds with no proposals — e.g. swarm_round_start signals)
  for (const round of debateRounds) {
    if (round.proposals && round.proposals.length > 0) {
      // Use a synthetic timestamp based on round number
      items.push({ kind: 'debate', timestamp: 2 + round.round, data: round })
    }
  }

  // Critiques (after debate)
  for (const critique of critiques) {
    const ts = critique.timestamp ? new Date(critique.timestamp).getTime() : Date.now()
    items.push({ kind: 'critique', timestamp: ts, data: critique })
  }

  // Revisions (after critique)
  for (const revision of revisions) {
    const ts = revision.timestamp ? new Date(revision.timestamp).getTime() : Date.now()
    items.push({ kind: 'revision', timestamp: ts, data: revision })
  }

  // Synthesis events - only show latest
  if (synthesisEvents.length > 0) {
    const latest = synthesisEvents[synthesisEvents.length - 1]
    items.push({ kind: 'synthesis', timestamp: latest.timestamp, data: latest })
  }

  // Completion check — near the end
  if (completionCheck) {
    const ts = completionCheck.timestamp ? new Date(completionCheck.timestamp).getTime() : Date.now()
    items.push({ kind: 'completion_check', timestamp: ts, data: completionCheck })
  }

  // XSkill events - group together
  if (xskillEvents.length > 0) {
    const latestTs = xskillEvents[xskillEvents.length - 1].timestamp
    items.push({ kind: 'xskill', timestamp: latestTs, data: xskillEvents })
  }

  // Tool creations
  for (const creation of toolCreations) {
    items.push({ kind: 'tool_creation', timestamp: creation.timestamp, data: creation })
  }

  // ORDERING FIX: Do NOT sort by timestamp — insertion order is correct.
  // Events arrive from backend in sequence order (monotonic counter).
  // Frontend Date.now() timestamps are unreliable due to batching delays
  // (batchedAddAgentThinking delays 16ms, tools are immediate → tools appear before thinking).
  // Instead: plan/deep_analysis/react_phase/agent_spawn have fixed positions (pushed first),
  // and all other items are already in correct order from processStore arrays.
  // Only sort the "fixed position" items (timestamp < 10) to keep them at top.
  const fixedItems = items.filter((i) => i.timestamp < 10)
  const dynamicItems = items.filter((i) => i.timestamp >= 10)
  // Fixed items sorted by their synthetic timestamps (0, 0.5, 0.6, 1, 2+)
  fixedItems.sort((a, b) => a.timestamp - b.timestamp)
  // EVENT-ORDER-FIX: Sort dynamic items by timestamp to correctly interleave
  // events of different types (thinking, tool_call, verify, etc.).
  // Previously, insertion order was assumed correct, but buildTimeline pushes
  // by type (all thinking, then all tools, etc.), which groups them incorrectly.
  // Timestamps are Date.now() at insertion — accurate enough for display ordering.
  dynamicItems.sort((a, b) => a.timestamp - b.timestamp)
  return [...fixedItems, ...dynamicItems]
  } catch (err) {
    // DEFENSIVE: buildTimeline catastrophic failure — return empty rather than crash
    console.warn('[ProcessStream] buildTimeline failed:', err)
    return []
  }
}

// convertExecutionEventsToTimeline is now imported from ./timeline-utils

function formatElapsed(startTime: number): string {
  const seconds = Math.floor((Date.now() - startTime) / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${(seconds % 60).toString().padStart(2, '0')}s`
}

// —— Component ——————————————————————————————————————

// STABLE EMPTY REFERENCES: Avoid creating new [] on every render in selectors.
// Zustand uses Object.is() equality — new [] !== old [] causes infinite re-renders.
const EMPTY_EVENTS: ExecutionEvent[] = []
const EMPTY_AGENT_MESSAGES: AgentMessage[] = []
const EMPTY_AGENTS: AgentInfo[] = []
const EMPTY_TOOL_EXECUTIONS: ToolExecution[] = []
const EMPTY_DEBATE_ROUNDS: DebateRound[] = []
const EMPTY_SYNTHESIS_EVENTS: SynthesisEvent[] = []
const EMPTY_XSKILL_EVENTS: XSkillEvent[] = []
const EMPTY_TOOL_CREATIONS: ToolCreationEvent[] = []
const EMPTY_AGENT_THINKING: Array<{ agentName: string; agentRole: string; content: string; timestamp: number }> = []
const EMPTY_SELF_REFLECTIONS: SelfReflectionResult[] = []
const EMPTY_VERIFICATIONS: AutoVerifyResult[] = []
const EMPTY_MID_ANALYSES: MidAnalysisResult[] = []
const EMPTY_CRITIQUES: CritiqueResult[] = []
const EMPTY_REVISIONS: RevisionResult[] = []

// Max tool call cards to show inline before collapsing older ones
const MAX_INLINE_TOOL_CARDS = 25
// Pagination step for showing/hiding older events
const EVENTS_PAGE_SIZE = 20

export function ProcessStream({ sessionId, compact = false }: ProcessStreamProps) {
  // —— ALL hooks MUST be called before any early return ————————
  const [showDetails, setShowDetails] = useState(!compact)
  const [extraVisibleCount, setExtraVisibleCount] = useState(0)
  const [isCancelling, setIsCancelling] = useState(false)

  const stage = useProcessStore((s) => s.stage)
  const storeSessionId = useProcessStore((s) => s.currentSessionId || s.sessionId)
  const taskId = useProcessStore((s) => s.taskId)
  const plan = useProcessStore((s) => s.plan)
  // STABLE REFERENCES: Use ?? EMPTY_* to guarantee stable reference when store returns undefined/null
  const agentMessages = useProcessStore((s) => s.agentMessages) ?? EMPTY_AGENT_MESSAGES
  const trackedAgentsRaw = useProcessStore((s) => s.agents) ?? EMPTY_AGENTS
  // FIX 4.4: Merge realtime agent statuses from WS into process-tracked agents
  const realtimeAgentStatuses = useRealtimeStore((s) => s.agentStatuses)
  const trackedAgents = useMemo(() => {
    if (!trackedAgentsRaw || trackedAgentsRaw.length === 0) return trackedAgentsRaw
    return trackedAgentsRaw.map((a) => {
      if (!a || !a.id) return a
      const live = realtimeAgentStatuses?.[a.id]
      if (live) {
        return {
          ...a,
          status: (live.status as typeof a.status) || a.status,
          lastActivityAt: live.timestamp || a.lastActivityAt,
        }
      }
      return a
    })
  }, [trackedAgentsRaw, realtimeAgentStatuses])
  const toolExecutions = useProcessStore((s) => s.toolExecutions) ?? EMPTY_TOOL_EXECUTIONS
  const debateRounds = useProcessStore((s) => s.debateRounds) ?? EMPTY_DEBATE_ROUNDS
  const synthesisEvents = useProcessStore((s) => s.synthesisEvents) ?? EMPTY_SYNTHESIS_EVENTS
  const xskillEvents = useProcessStore((s) => s.xskillEvents) ?? EMPTY_XSKILL_EVENTS
  const toolCreations = useProcessStore((s) => s.toolCreations) ?? EMPTY_TOOL_CREATIONS
  const agentThinkingEvents = useProcessStore((s) => s.agentThinkingEvents) ?? EMPTY_AGENT_THINKING
  const startTime = useProcessStore((s) => s.startTime)

  // Deep reasoning state
  const deepAnalysis = useProcessStore((s) => s.deepAnalysis)
  const selfReflections = useProcessStore((s) => s.selfReflections) ?? EMPTY_SELF_REFLECTIONS
  const verifications = useProcessStore((s) => s.verifications) ?? EMPTY_VERIFICATIONS
  const completionCheck = useProcessStore((s) => s.completionCheck)
  const midAnalyses = useProcessStore((s) => s.midAnalyses) ?? EMPTY_MID_ANALYSES
  const critiques = useProcessStore((s) => s.critiques) ?? EMPTY_CRITIQUES
  const revisions = useProcessStore((s) => s.revisions) ?? EMPTY_REVISIONS
  const reactPhase = useProcessStore((s) => s.reactPhase) ?? ''

  const transparencyLevel = usePreferenceStore((s) => s.transparencyLevel)

  // F5-SYNC-FIX: Read execution events as fallback source when processStore is empty
  // IMPORTANT: Use stable empty array to avoid infinite re-render (new [] each render)
  const executionEvents = useExecutionStore((s) => s.eventsBySession[sessionId]) ?? EMPTY_EVENTS

  const isActiveProcess = stage !== 'completed' && stage !== 'failed'

  // —— Stop/Cancel task handler ————————————————————————————————
  const handleCancelTask = useCallback(async () => {
    if (!taskId || isCancelling) return
    setIsCancelling(true)
    try {
      // Call backend cancel API
      await apiPost(`/tasks/${taskId}/cancel`, {})
      // Also call frontend SSE abort (if stream is active in ChatPanel)
      useProcessStore.getState().endProcess(false, 'Cancelled by user')
    } catch (err) {
      console.warn('[ProcessStream] Cancel failed:', err)
    } finally {
      setIsCancelling(false)
    }
  }, [taskId, isCancelling])

  const timeline = useMemo(
    () =>
      buildTimeline(
        plan,
        agentMessages,
        trackedAgents,
        toolExecutions,
        debateRounds,
        synthesisEvents,
        xskillEvents,
        toolCreations,
        agentThinkingEvents,
        isActiveProcess,
        deepAnalysis,
        selfReflections,
        verifications,
        completionCheck,
        midAnalyses,
        critiques,
        revisions,
        reactPhase,
      ),
    [plan, agentMessages, trackedAgents, toolExecutions, debateRounds, synthesisEvents, xskillEvents, toolCreations, agentThinkingEvents, isActiveProcess, deepAnalysis, selfReflections, verifications, completionCheck, midAnalyses, critiques, revisions, reactPhase],
  )

  // Filter timeline items based on transparency level
  const filteredTimeline = useMemo(() => {
    if (transparencyLevel === 'debug' || transparencyLevel === 'detailed') return timeline
    if (transparencyLevel === 'normal') {
      // Show most event types at normal level
      return timeline.filter((item) =>
        [
          'plan', 'agent_spawn', 'tool_call', 'debate', 'synthesis', 'xskill',
          'tool_creation', 'agent_thinking', 'agent_communication',
          'deep_analysis', 'self_reflection', 'completion_check',
          'critique', 'revision',
        ].includes(item.kind),
      )
    }
    // minimal — plan + synthesis + agent_thinking + deep_analysis + completion_check
    return timeline.filter((item) =>
      item.kind === 'plan' ||
      item.kind === 'synthesis' ||
      item.kind === 'agent_thinking' ||
      item.kind === 'deep_analysis' ||
      item.kind === 'completion_check',
    )
  }, [timeline, transparencyLevel])

  // F5-SYNC-FIX: Fallback timeline from executionStore when processStore is empty
  // executionStore is populated from metadata.execution_events which is ALWAYS complete
  const fallbackTimeline = useMemo(() => {
    // Only compute fallback if processStore timeline is empty but executionStore has data
    if (filteredTimeline.length > 0 || executionEvents.length === 0) return null
    return convertExecutionEventsToTimeline(executionEvents)
  }, [filteredTimeline.length, executionEvents])

  // FLICKER-FIX: Stabilize source switching — prevent flicker during resetDataArrays() rebuild.
  // If processStore JUST had data but is now momentarily empty (rebuild in progress),
  // don't immediately fallback to executionStore which shows different/stale content.
  const lastProcessStoreHadDataRef = useRef(false)
  const lastProcessStoreTimestampRef = useRef(0)

  useEffect(() => {
    if (filteredTimeline.length > 0) {
      lastProcessStoreHadDataRef.current = true
      lastProcessStoreTimestampRef.current = Date.now()
    }
  }, [filteredTimeline.length])

  // Supplemental orchestrator decision/insight events. buildTimeline (processStore)
  // does NOT produce these — they only arrive via DB polling into executionStore.
  // Without this, evaluation/plan/evolution/authorization/rollback cards would be
  // invisible during a LIVE run and only appear later in history (MessageEventExplorer).
  // Rendered through the SAME converter as history → live and history stay consistent.
  const supplementalTimeline = useMemo(
    () => buildSupplementalTimeline(executionEvents),
    [executionEvents],
  )

  // Effective timeline: prefer processStore (+ supplemental decision events),
  // fallback to executionStore entirely (with stabilization) when processStore is empty.
  const effectiveTimeline = useMemo(() => {
    if (filteredTimeline.length > 0) {
      if (supplementalTimeline.length === 0) return filteredTimeline
      // Merge supplemental decision/insight cards into the processStore timeline,
      // sorted by timestamp so they interleave at the right point.
      return [...filteredTimeline, ...supplementalTimeline].sort(
        (a, b) => a.timestamp - b.timestamp,
      )
    }
    // If processStore JUST had data (within 1000ms), don't fallback yet — likely a rebuild in progress
    if (lastProcessStoreHadDataRef.current && (Date.now() - lastProcessStoreTimestampRef.current < 1000)) {
      return [] // Show empty briefly rather than flicker to different source
    }
    // Fallback path already includes supplemental events (full convert).
    return fallbackTimeline || []
  }, [filteredTimeline, fallbackTimeline, supplementalTimeline])
  const hasExecutionData = executionEvents.length > 0

  // —— Early returns AFTER all hooks ———————————————————————————
  // Only render for matching session
  // F5-SYNC-FIX: Also allow rendering when executionStore has data (even if processStore sessionId hasn't synced yet)
  if (storeSessionId !== sessionId && !hasExecutionData) {
    console.log('[F5-Debug][ProcessStream] BLOCKED: storeSessionId mismatch', { storeSessionId, sessionId, stage, focusedTaskId: taskId })
    return null
  }
  if (stage === 'idle' && !hasExecutionData) {
    console.log('[F5-Debug][ProcessStream] BLOCKED: stage is idle and no execution data', { storeSessionId, sessionId, agentsCount: trackedAgentsRaw.length })
    return null
  }
  if (effectiveTimeline.length === 0) {
    // If process is actively running but no timeline items yet (early stage),
    // show a minimal "in progress" indicator so users know something is happening.
    if (stage !== 'idle' && stage !== 'completed' && stage !== 'failed') {
      return (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn(
            'rounded-lg border border-border bg-gray-50 px-3.5 py-2.5',
            'dark:bg-[#12122a] dark:border-[#2a2a45]',
          )}
        >
          <div className="flex items-center gap-2 text-xs">
            <Eye className="h-3.5 w-3.5 text-blue-500 animate-pulse shrink-0" />
            <span className="text-text-secondary dark:text-gray-400">
              Agent đang xử lý...
            </span>
            <Badge variant="default" size="sm" className="ml-auto capitalize">
              {stage}
            </Badge>
            {startTime && (
              <span className="text-text-muted dark:text-gray-500 flex items-center gap-0.5">
                <Timer className="h-3 w-3" />
                {formatElapsed(startTime)}
              </span>
            )}
          </div>
        </motion.div>
      )
    }
    return null
  }

  console.log('[F5-Debug][ProcessStream] RENDERING timeline items:', effectiveTimeline.length, 'source:', filteredTimeline.length > 0 ? 'processStore' : 'executionStore(fallback)')

  const isActive = isActiveProcess
  const isDone = stage === 'completed' || stage === 'failed'

  // Summary card when collapsed or completed
  if (!showDetails && isDone && effectiveTimeline.length > 0) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className={cn(
          'rounded-lg border border-border bg-gray-50 px-3.5 py-2.5',
          'dark:bg-[#12122a] dark:border-[#2a2a45]',
        )}
      >
        <button
          type="button"
          onClick={() => setShowDetails(true)}
          className="flex w-full items-center gap-2 text-left text-xs"
        >
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          <span className="text-text-secondary dark:text-gray-400">
            Process completed
          </span>
          <div className="flex items-center gap-1.5 ml-auto">
            {toolExecutions.length > 0 && (
              <Badge variant="default" size="sm">
                {toolExecutions.length} tools
              </Badge>
            )}
            {debateRounds.length > 0 && (
              <Badge variant="default" size="sm">
                {debateRounds.length} rounds
              </Badge>
            )}
            {startTime && (
              <span className="text-text-muted dark:text-gray-500 flex items-center gap-0.5">
                <Timer className="h-3 w-3" />
                {formatElapsed(startTime)}
              </span>
            )}
            <Eye className="h-3 w-3 text-text-muted dark:text-gray-500" />
          </div>
        </button>
      </motion.div>
    )
  }

  return (
    <div className="space-y-2">
      {/* Toggle header + Stop button */}
      {effectiveTimeline.length > 0 && (
        <div className="flex items-center justify-between gap-2">
          {/* Stop button — only when task is actively running */}
          {isActive && taskId && !taskId.startsWith('msg-') && (
            <button
              type="button"
              onClick={handleCancelTask}
              disabled={isCancelling}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors',
                'text-red-600 hover:text-red-700 hover:bg-red-50',
                'dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-900/20',
                isCancelling && 'opacity-50 cursor-not-allowed',
              )}
              title="Dừng tiến trình này"
            >
              <StopCircle className="h-3.5 w-3.5" />
              {isCancelling ? 'Đang dừng...' : 'Dừng'}
            </button>
          )}
          {!isActive && <div />}

          <button
            type="button"
            onClick={() => setShowDetails((p) => !p)}
            className="flex items-center gap-1 text-[11px] text-text-muted dark:text-gray-500 hover:text-text-secondary dark:hover:text-gray-400 transition-colors"
          >
            {showDetails ? (
              <>
                <EyeOff className="h-3 w-3" />
                Summary only
              </>
            ) : (
              <>
                <Eye className="h-3 w-3" />
                Show all details
              </>
            )}
          </button>
        </div>
      )}

      {/* Timeline cards — limited to MAX_INLINE_TOOL_CARDS unless expanded */}
      <AnimatePresence mode="popLayout">
        {showDetails ? (
          (() => {
            const maxVisible = MAX_INLINE_TOOL_CARDS + extraVisibleCount
            const visibleItems = maxVisible >= effectiveTimeline.length
              ? effectiveTimeline
              : effectiveTimeline.slice(-maxVisible)
            const hiddenCount = effectiveTimeline.length - visibleItems.length
            const canHide = extraVisibleCount > 0
            return (
              <>
                {/* Show more / Show all buttons */}
                {hiddenCount > 0 && (
                  <motion.div
                    key="show-more-btns"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className={cn(
                      'w-full flex items-center justify-center gap-2 py-1.5 text-[11px] font-medium rounded-lg',
                      'bg-blue-50/60 dark:bg-blue-900/10',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setExtraVisibleCount((c) => c + EVENTS_PAGE_SIZE)}
                      className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 transition-colors"
                    >
                      ↑ Show {Math.min(EVENTS_PAGE_SIZE, hiddenCount)} older
                    </button>
                    {hiddenCount > EVENTS_PAGE_SIZE && (
                      <>
                        <span className="text-gray-300 dark:text-gray-600">|</span>
                        <button
                          type="button"
                          onClick={() => setExtraVisibleCount(effectiveTimeline.length)}
                          className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 transition-colors"
                        >
                          ↑ Show all {hiddenCount}
                        </button>
                      </>
                    )}
                  </motion.div>
                )}
                {/* Hide / Collapse buttons */}
                {canHide && (
                  <motion.div
                    key="hide-btns"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className={cn(
                      'w-full flex items-center justify-center gap-2 py-1.5 text-[11px] font-medium rounded-lg',
                      'bg-orange-50/60 dark:bg-orange-900/10',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setExtraVisibleCount((c) => Math.max(0, c - EVENTS_PAGE_SIZE))}
                      className="text-orange-600 hover:text-orange-800 dark:text-orange-400 dark:hover:text-orange-300 transition-colors"
                    >
                      ↓ Hide {EVENTS_PAGE_SIZE} events
                    </button>
                    <span className="text-gray-300 dark:text-gray-600">|</span>
                    <button
                      type="button"
                      onClick={() => setExtraVisibleCount(0)}
                      className="text-orange-600 hover:text-orange-800 dark:text-orange-400 dark:hover:text-orange-300 transition-colors"
                    >
                      ↓ Hide all extra
                    </button>
                  </motion.div>
                )}
                {visibleItems.map((item, idx) => {
                  const key = `${item.kind}-${idx}`
                  // Only animate items near the end to reduce layout thrash
                  const shouldAnimate = idx >= visibleItems.length - 5
                  // Stagger delay based on position from bottom
                  const staggerDelay = shouldAnimate
                    ? Math.max(0, (idx - (visibleItems.length - 5))) * 0.06
                    : 0
                  return (
                    <motion.div
                      key={key}
                      layout={shouldAnimate ? 'position' : false}
                      initial={shouldAnimate ? { opacity: 0, y: 20, scale: 0.95, filter: 'blur(4px)' } : false}
                      animate={{ opacity: 1, y: 0, scale: 1, filter: 'blur(0px)' }}
                      exit={{ opacity: 0, y: -12, scale: 0.95, filter: 'blur(2px)' }}
                      transition={{
                        type: 'spring',
                        stiffness: 380,
                        damping: 28,
                        mass: 0.8,
                        delay: staggerDelay,
                      }}
                    >
                      <TimelineCard item={item} />
                    </motion.div>
                  )
                })}
              </>
            )
          })()
        ) : (
          // Compact summary
          <motion.div
            key="summary"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className={cn(
              'rounded-lg border border-border bg-gray-50 px-3.5 py-2 text-xs',
              'dark:bg-[#12122a] dark:border-[#2a2a45]',
              'flex items-center gap-2 flex-wrap',
            )}
          >
            {isActive && (
              <Badge variant="info" size="sm" dot>
                {stage}
              </Badge>
            )}
            {plan && <Badge variant="brand" size="sm">{plan.strategy}</Badge>}
            {deepAnalysis && (
              <span className="text-text-muted dark:text-gray-500">
                🔬 Deep Analysis
              </span>
            )}
            {toolExecutions.length > 0 && (
              <span className="text-text-muted dark:text-gray-500">
                🔧 {toolExecutions.length} tools
              </span>
            )}
            {debateRounds.length > 0 && (
              <span className="text-text-muted dark:text-gray-500">
                ⚔️ {debateRounds.length} rounds
              </span>
            )}
            {critiques.length > 0 && (
              <span className="text-text-muted dark:text-gray-500">
                🎯 {critiques.length} critiques
              </span>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
