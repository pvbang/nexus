// ============================================================
// Nexus - Unified Timeline
// Chronological timeline merging processStore + executionStore
// ============================================================

// Stable empty arrays to avoid infinite re-render loops in Zustand selectors
const EMPTY_EVENTS: never[] = []

import { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import {
  ChevronDown, ChevronRight, Search, Filter,
  CheckCircle2, Loader2, XCircle, Clock,
  ScrollText,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { sanitizeDebateContent } from '../../lib/utils'
import { getAgentColor } from '../../lib/constants'
import { getToolSource } from './cards/tool-renderers'
import { useProcessStore } from '../../stores/processStore'
import { useExecutionStore } from '../../stores/executionStore'
import { usePreferenceStore } from '../../stores/preferenceStore'
import type { ExecutionEvent } from '../../types/execution'

// ── Types ─────────────────────────────────────────────────────

interface UnifiedTimelineProps {
  sessionId: string
  className?: string
}

interface TimelineEntry {
  id: string
  timestamp: number
  stage: string
  type:
    | 'step'
    | 'agent_spawn'
    | 'agent_thinking'
    | 'tool_start'
    | 'tool_done'
    | 'agent_communication'
    | 'swarm_round'
    | 'synthesis'
    | 'xskill'
    | 'tool_creation'
    | 'plan'
    | 'debate_proposal'
    | 'debate_rebuttal'
    | 'debate_verdict'
    | 'debate_critique'
    | 'debate_revision'
    | 'deep_analysis'
    | 'self_reflection'
    | 'auto_verify'
    | 'completion_check'
    | 'mid_analysis'
    | 'react_phase'
  title: string
  description: string
  metadata?: Record<string, unknown>
  status?: 'active' | 'done' | 'error' | 'pending'
}

// ── Constants ─────────────────────────────────────────────────

const STAGE_ORDER = [
  'planning',
  'spawning',
  'executing',
  'debating',
  'synthesizing',
  'learning',
] as const

const STAGE_LABELS: Record<string, string> = {
  planning: 'Planning',
  spawning: 'Spawning',
  executing: 'Executing',
  debating: 'Debating',
  synthesizing: 'Synthesizing',
  learning: 'Learning',
}

const TYPE_LABELS: Record<TimelineEntry['type'], string> = {
  step: 'Step',
  agent_spawn: 'Agent Spawn',
  agent_thinking: 'Thinking',
  tool_start: 'Tool Start',
  tool_done: 'Tool Done',
  agent_communication: 'Communication',
  swarm_round: 'Swarm Round',
  synthesis: 'Synthesis',
  xskill: 'XSkill',
  tool_creation: 'Tool Creation',
  plan: 'Plan',
  debate_proposal: 'Proposal',
  debate_rebuttal: 'Rebuttal',
  debate_verdict: 'Verdict',
  debate_critique: 'Critique',
  debate_revision: 'Revision',
  deep_analysis: 'Analysis',
  self_reflection: 'Reflection',
  auto_verify: 'Verify',
  completion_check: 'Check',
  mid_analysis: 'Mid-Analysis',
  react_phase: 'Phase',
}

// ── Helpers ───────────────────────────────────────────────────

function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts)
  if (diff < 1000) return 'just now'
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.floor(minutes / 60)}h ago`
}

function statusIndicator(status?: TimelineEntry['status']) {
  switch (status) {
    case 'done':
      return <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
    case 'active':
      return <Loader2 className="w-3 h-3 text-blue-500 animate-spin shrink-0" />
    case 'error':
      return <XCircle className="w-3 h-3 text-red-500 shrink-0" />
    default:
      return <Clock className="w-3 h-3 text-gray-400 dark:text-gray-500 shrink-0" />
  }
}

// ── Data merging ──────────────────────────────────────────────

function buildProcessEntries(state: {
  plan: ReturnType<typeof useProcessStore.getState>['plan']
  agentMessages: ReturnType<typeof useProcessStore.getState>['agentMessages']
  toolExecutions: ReturnType<typeof useProcessStore.getState>['toolExecutions']
  debateRounds: ReturnType<typeof useProcessStore.getState>['debateRounds']
  synthesisEvents: ReturnType<typeof useProcessStore.getState>['synthesisEvents']
  xskillEvents: ReturnType<typeof useProcessStore.getState>['xskillEvents']
  toolCreations: ReturnType<typeof useProcessStore.getState>['toolCreations']
  agentThinkingEvents: ReturnType<typeof useProcessStore.getState>['agentThinkingEvents']
  startTime: number | null
}): TimelineEntry[] {
  const entries: TimelineEntry[] = []

  // Plan
  if (state.plan) {
    entries.push({
      id: `plan-${state.plan.taskId}`,
      timestamp: state.startTime ?? Date.now(),
      stage: 'planning',
      type: 'plan',
      title: `Plan created — "${state.plan.strategy}" strategy`,
      description: `${state.plan.steps?.length ?? 0} steps${state.plan.complexity ? `, complexity: ${state.plan.complexity}` : ''}`,
      status: 'done',
    })
  }

  // Agent messages
  for (const msg of state.agentMessages) {
    entries.push({
      id: `msg-${msg.id}`,
      timestamp: msg.timestamp,
      stage: 'executing',
      type: 'agent_communication',
      title: `${msg.fromAgent.name} → ${msg.toAgent?.name ?? 'broadcast'}`,
      description: msg.content.slice(0, 120) + (msg.content.length > 120 ? '…' : ''),
      metadata: { messageType: msg.type, fromRole: msg.fromAgent.role },
      status: 'done',
    })
  }

  // Tool executions
  for (const tool of state.toolExecutions) {
    entries.push({
      id: `tool-${tool.id}`,
      timestamp: tool.timestamp,
      stage: 'executing',
      type: tool.status === 'running' ? 'tool_start' : 'tool_done',
      title: `${tool.agentName} → ${tool.toolName}`,
      description: tool.status === 'completed'
        ? `✅ ${tool.duration ? `${(tool.duration / 1000).toFixed(1)}s` : 'done'}`
        : tool.status === 'failed'
          ? `❌ ${tool.error ?? 'failed'}`
          : '⏳ running…',
      metadata: { args: tool.toolArgs },
      status: tool.status === 'running' ? 'active' : tool.status === 'failed' ? 'error' : 'done',
    })
  }

  // Debate rounds (skip empty rounds with no proposals)
  for (const round of state.debateRounds) {
    if (round.proposals && round.proposals.length > 0) {
      entries.push({
        id: `debate-${round.round}`,
        timestamp: Date.now() - (state.debateRounds.length - round.round) * 1000,
        stage: 'debating',
        type: 'swarm_round',
        title: `Round ${round.round}: ${round.proposals.length} proposals`,
        description: round.verdict
          ? `Verdict: ${round.verdict.summary}`
          : 'Deliberating…',
        status: round.verdict ? 'done' : 'active',
      })
    }
  }

  // Synthesis events
  for (const syn of state.synthesisEvents) {
    entries.push({
      id: `syn-${syn.id}`,
      timestamp: syn.timestamp,
      stage: 'synthesizing',
      type: 'synthesis',
      title: syn.type === 'done'
        ? `Synthesis done${syn.sourcesCount ? `: ${syn.sourcesCount} sources` : ''}`
        : syn.type === 'start' ? 'Synthesis started' : 'Synthesis in progress',
      description: syn.summary
        ? syn.summary.slice(0, 100)
        : syn.confidence ? `Confidence: ${syn.confidence}%` : '',
      status: syn.type === 'done' ? 'done' : 'active',
    })
  }

  // XSkill events
  for (const xs of state.xskillEvents) {
    entries.push({
      id: `xskill-${xs.id}`,
      timestamp: xs.timestamp,
      stage: 'learning',
      type: 'xskill',
      title: xs.type === 'retrieve'
        ? `XSkill: "${xs.skillName ?? 'unknown'}" retrieved`
        : xs.type === 'accumulate'
          ? `Experience accumulated${xs.quality ? ` (quality: ${xs.quality}/5)` : ''}`
          : `Critique${xs.details ? `: ${xs.details.slice(0, 60)}` : ''}`,
      description: xs.details ?? '',
      status: 'done',
    })
  }

  // Tool creations
  for (const tc of state.toolCreations) {
    entries.push({
      id: `tc-${tc.id}`,
      timestamp: tc.timestamp,
      stage: 'executing',
      type: 'tool_creation',
      title: `Tool created: "${tc.toolName}"`,
      description: tc.reason,
      status: tc.status === 'ready' ? 'done' : tc.status === 'failed' ? 'error' : 'active',
    })
  }

  // Agent thinking
  for (const th of state.agentThinkingEvents) {
    entries.push({
      id: `think-${th.agentName}-${th.timestamp}`,
      timestamp: th.timestamp,
      stage: 'executing',
      type: 'agent_thinking',
      title: `${th.agentName} thinking`,
      description: th.content.slice(0, 120) + (th.content.length > 120 ? '…' : ''),
      metadata: { role: th.agentRole },
      status: 'done',
    })
  }

  return entries
}

/** Event types that are noise and should NOT appear in the timeline */
const NOISE_EXEC_TYPES = new Set(['token', 'done', 'error', 'usage', 'heartbeat', 'pong', 'ping'])

function buildExecutionEntries(events: ExecutionEvent[]): TimelineEntry[] {
  const entries: TimelineEntry[] = []

  for (const evt of events) {
    // NOISE-FILTER: Skip low-value events that pollute the timeline
    if (NOISE_EXEC_TYPES.has(String(evt.type))) continue
    // Also skip step events with empty title (noise fallback from mapper)
    if (evt.type === 'step' && !(evt as any).title) continue

    switch (evt.type) {
      case 'step':
        entries.push({
          id: `exec-step-${evt.step}`,
          timestamp: evt.timestamp,
          stage: 'planning',
          type: 'step',
          title: evt.title,
          description: evt.description,
          status: evt.status,
        })
        break

      case 'agent_spawn':
        entries.push({
          id: `exec-spawn-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'spawning',
          type: 'agent_spawn',
          title: `Agent "${evt.agent_name}" spawned`,
          description: evt.agent_role,
          status: 'done',
        })
        break

      case 'thinking':
        entries.push({
          id: `exec-think-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'executing',
          type: 'agent_thinking',
          title: `${evt.agent_name} thinking`,
          description: evt.content.slice(0, 120) + (evt.content.length > 120 ? '…' : ''),
          metadata: { role: evt.agent_role },
          status: 'done',
        })
        break

      case 'tool_start': {
        // After tool_done is merged into tool_start, _merged=true and output exists.
        // Check these to show completed status instead of perpetual spinner.
        const isMerged = (evt as import('../../types/execution').ToolEvent)._merged === true
        const hasOutput = (evt as import('../../types/execution').ToolEvent).output !== undefined
        const toolSuccess = (evt as import('../../types/execution').ToolEvent).success
        const isToolCompleted = isMerged || hasOutput
        const isToolError = toolSuccess === false
        const toolExecTime = (evt as import('../../types/execution').ToolEvent).execution_time_ms

        let toolDescription = evt.params_preview ?? 'starting…'
        if (isToolCompleted && !isToolError) {
          toolDescription = `✅ ${toolExecTime ? `${(toolExecTime / 1000).toFixed(1)}s` : 'done'}`
        } else if (isToolError) {
          toolDescription = `❌ ${(evt as import('../../types/execution').ToolEvent).output_preview ?? 'failed'}`
        }

        entries.push({
          id: `exec-ts-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'executing',
          type: isToolCompleted ? 'tool_done' : 'tool_start',
          title: `${evt.agent_name} → ${evt.tool}`,
          description: toolDescription,
          status: isToolError ? 'error' : isToolCompleted ? 'done' : 'active',
        })
        break
      }

      case 'tool_done':
        entries.push({
          id: `exec-td-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'executing',
          type: 'tool_done',
          title: `${evt.agent_name} → ${evt.tool}`,
          description: evt.success
            ? `✅ ${evt.execution_time_ms ? `${(evt.execution_time_ms / 1000).toFixed(1)}s` : 'done'}`
            : `❌ ${evt.output_preview ?? 'failed'}`,
          status: evt.success ? 'done' : 'error',
        })
        break

      case 'swarm_round':
        entries.push({
          id: `exec-swarm-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'debating',
          type: 'swarm_round',
          title: `Round ${evt.round}/${evt.total_rounds}: ${evt.proposals.length} proposals`,
          description: evt.verdict?.synthesis ?? evt.verdict?.reasoning ?? 'Deliberating…',
          status: evt.verdict ? 'done' : 'active',
        })
        break

      // ── Granular debate events ─────────────────────────────
      case 'debate_proposal':
        entries.push({
          id: `exec-dp-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'debating',
          type: 'debate_proposal',
          title: `🗣️ ${evt.agent_name} proposes (Round ${evt.round})`,
          description: evt.content.slice(0, 120) + (evt.content.length > 120 ? '…' : ''),
          metadata: { agent_role: evt.agent_role, tools_used: evt.tools_used },
          status: 'done',
        })
        break

      case 'debate_rebuttal':
        entries.push({
          id: `exec-dr-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'debating',
          type: 'debate_rebuttal',
          title: `⚔️ ${evt.agent_name} rebuts (Round ${evt.round})`,
          description: evt.content.slice(0, 120) + (evt.content.length > 120 ? '…' : ''),
          status: 'done',
        })
        break

      case 'debate_verdict':
        entries.push({
          id: `exec-dv-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'debating',
          type: 'debate_verdict',
          title: `🧑‍⚖️ Verdict (Round ${evt.round})`,
          description: (evt.verdict?.synthesis as string) ?? (evt.verdict?.reasoning as string) ?? 'Verdict reached',
          metadata: evt.verdict,
          status: 'done',
        })
        break

      case 'debate_critique':
        entries.push({
          id: `exec-dc-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'debating',
          type: 'debate_critique',
          title: `📝 Critique${evt.data?.agent_name ? ` by ${evt.data.agent_name}` : ''}`,
          description: evt.data?.overall_quality != null
            ? `Quality: ${evt.data.overall_quality}/5`
            : 'Critique submitted',
          metadata: evt.data,
          status: 'done',
        })
        break

      case 'debate_revision': {
        const revText = sanitizeDebateContent(String(evt.data?.revised_content || evt.data?.content || ''))
        entries.push({
          id: `exec-drev-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'debating',
          type: 'debate_revision',
          title: `🔄 Revision${evt.data?.agent_name ? ` by ${evt.data.agent_name}` : ''}`,
          description: revText ? revText.slice(0, 100) : 'Content revised',
          metadata: evt.data,
          status: 'done',
        })
        break
      }

      // ── Deep reasoning events ──────────────────────────────
      case 'deep_analysis':
        entries.push({
          id: `exec-da-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'planning',
          type: 'deep_analysis',
          title: '🔍 Deep Analysis',
          description: evt.data?.strategy
            ? `Strategy: ${String(evt.data.strategy).replace(/(?<=\S)\s*(\d+\))/g, '\n$1')}`
            : `${(evt.data?.dimensions as string[])?.length ?? 0} dimensions analyzed`,
          metadata: evt.data,
          status: 'done',
        })
        break

      case 'self_reflection':
        entries.push({
          id: `exec-sr-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'executing',
          type: 'self_reflection',
          title: `🪞 Self-Reflection${evt.data?.round != null ? ` (Round ${evt.data.round})` : ''}`,
          description: evt.data?.on_track === false
            ? `Off track — ${evt.data.strategy_change ?? 'adjusting'}`
            : `Quality: ${evt.data?.quality_score ?? '?'}/5`,
          metadata: evt.data,
          status: 'done',
        })
        break

      case 'auto_verify':
        entries.push({
          id: `exec-av-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'executing',
          type: 'auto_verify',
          title: `✅ Verify: ${evt.data?.tool_name ?? 'result'}`,
          description: `Result: ${evt.data?.verification_result ?? 'unknown'}`,
          metadata: evt.data,
          status: evt.data?.verification_result === 'fail' ? 'error' : 'done',
        })
        break

      case 'completion_check':
        entries.push({
          id: `exec-cc-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'synthesizing',
          type: 'completion_check',
          title: '📋 Completion Check',
          description: evt.data?.completeness_pct != null
            ? `${evt.data.completeness_pct}% complete — ${evt.data.verdict ?? 'checking'}`
            : 'Checking completeness…',
          metadata: evt.data,
          status: evt.data?.verdict === 'fail' ? 'error' : 'done',
        })
        break

      case 'mid_analysis':
        entries.push({
          id: `exec-ma-${evt.id}`,
          timestamp: evt.timestamp,
          stage: 'executing',
          type: 'mid_analysis',
          title: `📊 Mid-Analysis${evt.data?.round != null ? ` (Round ${evt.data.round})` : ''}`,
          description: evt.data?.on_track === false
            ? `Off track — ${evt.data.strategy_adjustment ?? 'adjusting'}`
            : 'On track',
          metadata: evt.data,
          status: 'done',
        })
        break

      case 'react_phase':
        // Skip — spammy, fires every loop iteration
        break
    }
  }

  return entries
}

// ── Component ─────────────────────────────────────────────────

export function UnifiedTimeline({ sessionId, className }: UnifiedTimelineProps) {
  // -- Store selectors --
  const plan = useProcessStore((s) => s.plan)
  const agentMessages = useProcessStore((s) => s.agentMessages)
  const toolExecutions = useProcessStore((s) => s.toolExecutions)
  const debateRounds = useProcessStore((s) => s.debateRounds)
  const synthesisEvents = useProcessStore((s) => s.synthesisEvents)
  const xskillEvents = useProcessStore((s) => s.xskillEvents)
  const toolCreations = useProcessStore((s) => s.toolCreations)
  const agentThinkingEvents = useProcessStore((s) => s.agentThinkingEvents)
  const startTime = useProcessStore((s) => s.startTime)
  const processStage = useProcessStore((s) => s.stage)

  const executionEvents = useExecutionStore((s) => s.eventsBySession[sessionId] ?? EMPTY_EVENTS)
  const isActive = useExecutionStore((s) => s.activeBySession[sessionId] ?? false)
    || (processStage !== 'idle' && processStage !== 'completed' && processStage !== 'failed')

  // -- Local state --
  const [searchQuery, setSearchQuery] = useState('')
  const [filterTypes, setFilterTypes] = useState<Set<TimelineEntry['type']>>(new Set())
  const [showFilterMenu, setShowFilterMenu] = useState(false)
  const [collapsedStages, setCollapsedStages] = useState<Set<string>>(new Set())
  const [userScrolled, setUserScrolled] = useState(false)
  // Performance: limit visible events per stage, expand on demand (paginated)
  const MAX_VISIBLE_PER_STAGE = 30
  const STAGE_PAGE_SIZE = 20
  const [stageExtraVisible, setStageExtraVisible] = useState<Map<string, number>>(new Map())

  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // -- Merge all entries --
  const allEntries = useMemo(() => {
    const processEntries = buildProcessEntries({
      plan, agentMessages, toolExecutions, debateRounds,
      synthesisEvents, xskillEvents, toolCreations,
      agentThinkingEvents, startTime,
    })
    const execEntries = buildExecutionEntries(executionEvents)

    // DEDUP-FIX: When BOTH stores have data, they represent the same events from
    // different sources (DB load vs live SSE). Prefer executionStore as canonical.
    // Only add processStore entries that have NO semantic match in executionStore.
    const merged: TimelineEntry[] = []

    // Add execution entries first (priority — canonical from DB)
    for (const entry of execEntries) {
      merged.push(entry)
    }

    // Build a signature set from execution entries for semantic dedup
    // Signature: type + title (contains agent + tool/content info)
    const execSignatures = new Set<string>()
    for (const entry of execEntries) {
      // Use type + truncated title as signature (covers same agent+tool combos)
      execSignatures.add(`${entry.type}::${entry.title.slice(0, 80)}`)
    }

    // Add process entries that don't semantically duplicate execution entries
    for (const entry of processEntries) {
      const signature = `${entry.type}::${entry.title.slice(0, 80)}`
      // Skip if executionStore already has a matching entry
      if (execSignatures.has(signature)) continue
      // Also skip by exact id
      if (merged.some((m) => m.id === entry.id)) continue
      merged.push(entry)
    }

    // Stable sort: when timestamps are equal, preserve original insertion order (index)
    return merged
      .map((entry, i) => ({ entry, originalIdx: i }))
      .sort((a, b) => {
        const timeDiff = a.entry.timestamp - b.entry.timestamp
        return timeDiff !== 0 ? timeDiff : a.originalIdx - b.originalIdx
      })
      .map(({ entry }) => entry)
  }, [
    plan, agentMessages, toolExecutions, debateRounds,
    synthesisEvents, xskillEvents, toolCreations,
    agentThinkingEvents, startTime, executionEvents,
  ])

  // -- Transparency level filtering --
  const transparencyLevel = usePreferenceStore((s) => s.transparencyLevel)

  const levelFilteredEntries = useMemo(() => {
    if (transparencyLevel === 'debug' || transparencyLevel === 'detailed') return allEntries
    if (transparencyLevel === 'normal') {
      return allEntries.filter((e) =>
        [
          'step', 'agent_spawn', 'tool_start', 'tool_done', 'swarm_round',
          'plan', 'synthesis', 'xskill', 'tool_creation',
          // Debate events — always visible at normal level
          'debate_proposal', 'debate_rebuttal', 'debate_verdict',
          'debate_critique', 'debate_revision',
          // Deep reasoning highlights
          'deep_analysis', 'completion_check',
        ].includes(e.type),
      )
    }
    // minimal — only step + plan + debate verdict (most important)
    return allEntries.filter((e) => ['step', 'plan', 'debate_verdict', 'completion_check'].includes(e.type))
  }, [allEntries, transparencyLevel])

  // -- Filtered entries (search + type filter on top of transparency) --
  const filteredEntries = useMemo(() => {
    let result = levelFilteredEntries
    if (filterTypes.size > 0) {
      result = result.filter((e) => filterTypes.has(e.type))
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      result = result.filter(
        (e) => (e.title || '').toLowerCase().includes(q) || (e.description || '').toLowerCase().includes(q),
      )
    }
    return result
  }, [levelFilteredEntries, filterTypes, searchQuery])

  // -- Group by stage --
  const groupedByStage = useMemo(() => {
    const groups: Record<string, TimelineEntry[]> = {}
    for (const entry of filteredEntries) {
      if (!groups[entry.stage]) groups[entry.stage] = []
      groups[entry.stage].push(entry)
    }
    // Return in stage order
    return STAGE_ORDER
      .filter((stage) => groups[stage]?.length)
      .map((stage) => ({ stage, entries: groups[stage] }))
  }, [filteredEntries])

  // -- Auto-scroll --
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setUserScrolled(!atBottom)
  }, [])

  useEffect(() => {
    if (isActive && !userScrolled) {
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
      })
    }
  }, [filteredEntries.length, isActive, userScrolled])

  // -- Toggle helpers --
  const toggleStage = (stage: string) => {
    setCollapsedStages((prev) => {
      const next = new Set(prev)
      if (next.has(stage)) next.delete(stage)
      else next.add(stage)
      return next
    })
  }

  const toggleFilter = (type: TimelineEntry['type']) => {
    setFilterTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  // -- Empty state --
  if (allEntries.length === 0) {
    return (
      <div className={cn('flex flex-col items-center justify-center h-full px-4 text-center', className)}>
        <ScrollText className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-3" />
        <p className="text-[13px] font-medium text-gray-500 dark:text-gray-400">
          No execution events yet
        </p>
        <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
          Events will appear here when a task is being processed.
        </p>
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col h-full', className)}>
      {/* ── Header: count, filter, search ─────────────────────── */}
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2 border-b shrink-0',
          'border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400 shrink-0">
          {filteredEntries.length} event{filteredEntries.length !== 1 ? 's' : ''}
        </span>

        {/* Filter dropdown */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setShowFilterMenu((v) => !v)}
            className={cn(
              'flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] transition-colors',
              filterTypes.size > 0
                ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                : 'bg-gray-100 text-gray-600 dark:bg-[#1a1a2e] dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-[#22223a]',
            )}
          >
            <Filter className="w-3 h-3" />
            Filter{filterTypes.size > 0 ? ` (${filterTypes.size})` : ''}
          </button>
          {showFilterMenu && (
            <div
              className={cn(
                'absolute top-full left-0 mt-1 z-20 w-44 py-1 rounded-lg shadow-lg border',
                'bg-white border-gray-200 dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
              )}
            >
              {(Object.keys(TYPE_LABELS) as TimelineEntry['type'][]).map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => toggleFilter(type)}
                  className={cn(
                    'flex items-center gap-2 w-full px-3 py-1.5 text-left text-[11px] transition-colors',
                    'hover:bg-gray-50 dark:hover:bg-[#22223a]',
                    filterTypes.has(type)
                      ? 'text-blue-700 dark:text-blue-300 font-medium'
                      : 'text-gray-600 dark:text-gray-400',
                  )}
                >
                  <span className={cn(
                    'w-3 h-3 rounded border flex items-center justify-center',
                    filterTypes.has(type)
                      ? 'bg-blue-500 border-blue-500'
                      : 'border-gray-300 dark:border-gray-600',
                  )}>
                    {filterTypes.has(type) && (
                      <CheckCircle2 className="w-2.5 h-2.5 text-white" />
                    )}
                  </span>
                  {TYPE_LABELS[type]}
                </button>
              ))}
              {filterTypes.size > 0 && (
                <button
                  type="button"
                  onClick={() => setFilterTypes(new Set())}
                  className="w-full px-3 py-1.5 text-left text-[10px] text-red-500 hover:bg-gray-50 dark:hover:bg-[#22223a] border-t border-gray-100 dark:border-[#2a2a45]"
                >
                  Clear filters
                </button>
              )}
            </div>
          )}
        </div>

        {/* Search */}
        <div className="flex-1 min-w-0">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400 dark:text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search…"
              className={cn(
                'w-full pl-6 pr-2 py-1 rounded-md text-[11px] outline-none transition-colors',
                'bg-gray-100 text-gray-700 placeholder:text-gray-400',
                'dark:bg-[#1a1a2e] dark:text-gray-200 dark:placeholder:text-gray-500',
                'focus:ring-1 focus:ring-blue-400 dark:focus:ring-blue-600',
              )}
            />
          </div>
        </div>
      </div>

      {/* ── Close filter menu on outside click ─────────────────── */}
      {showFilterMenu && (
        <div
          className="fixed inset-0 z-10"
          onClick={() => setShowFilterMenu(false)}
        />
      )}

      {/* ── Timeline body ──────────────────────────────────────── */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto min-h-0 px-3 py-2 space-y-2"
      >
        {groupedByStage.map(({ stage, entries }) => {
          const isCollapsed = collapsedStages.has(stage)
          return (
            <div key={stage}>
              {/* Stage header */}
              <button
                type="button"
                onClick={() => toggleStage(stage)}
                className={cn(
                  'flex items-center gap-2 w-full px-3 py-2 rounded-lg text-left transition-colors',
                  'bg-gray-50 hover:bg-gray-100 dark:bg-[#12122a] dark:hover:bg-[#1a1a35]',
                )}
              >
                {isCollapsed
                  ? <ChevronRight className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 shrink-0" />
                  : <ChevronDown className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 shrink-0" />
                }
                <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
                  {STAGE_LABELS[stage] ?? stage}
                </span>
                <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-auto">
                  {entries.length} event{entries.length !== 1 ? 's' : ''}
                </span>
              </button>

              {/* Events — limited to MAX_VISIBLE_PER_STAGE unless expanded (paginated) */}
              {!isCollapsed && (() => {
                const extraCount = stageExtraVisible.get(stage) ?? 0
                const maxVisible = MAX_VISIBLE_PER_STAGE + extraCount
                const visible = maxVisible >= entries.length ? entries : entries.slice(-maxVisible)
                const hiddenCount = entries.length - visible.length
                const canHide = extraCount > 0
                return (
                  <div className="ml-3 mt-1 space-y-0.5">
                    {/* Show more / Show all */}
                    {hiddenCount > 0 && (
                      <div className="flex items-center gap-2 px-3 py-1">
                        <button
                          type="button"
                          onClick={() => setStageExtraVisible((prev) => {
                            const next = new Map(prev)
                            next.set(stage, (prev.get(stage) ?? 0) + STAGE_PAGE_SIZE)
                            return next
                          })}
                          className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline"
                        >
                          ↑ Show {Math.min(STAGE_PAGE_SIZE, hiddenCount)} older
                        </button>
                        {hiddenCount > STAGE_PAGE_SIZE && (
                          <>
                            <span className="text-[10px] text-gray-300 dark:text-gray-600">|</span>
                            <button
                              type="button"
                              onClick={() => setStageExtraVisible((prev) => {
                                const next = new Map(prev)
                                next.set(stage, entries.length)
                                return next
                              })}
                              className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline"
                            >
                              ↑ Show all {hiddenCount}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                    {/* Hide / Collapse buttons */}
                    {canHide && (
                      <div className="flex items-center gap-2 px-3 py-1">
                        <button
                          type="button"
                          onClick={() => setStageExtraVisible((prev) => {
                            const next = new Map(prev)
                            const current = prev.get(stage) ?? 0
                            const newVal = Math.max(0, current - STAGE_PAGE_SIZE)
                            if (newVal === 0) next.delete(stage)
                            else next.set(stage, newVal)
                            return next
                          })}
                          className="text-[10px] text-orange-600 dark:text-orange-400 hover:underline"
                        >
                          ↓ Hide {STAGE_PAGE_SIZE} events
                        </button>
                        <span className="text-[10px] text-gray-300 dark:text-gray-600">|</span>
                        <button
                          type="button"
                          onClick={() => setStageExtraVisible((prev) => {
                            const next = new Map(prev)
                            next.delete(stage)
                            return next
                          })}
                          className="text-[10px] text-orange-600 dark:text-orange-400 hover:underline"
                        >
                          ↓ Hide all extra
                        </button>
                      </div>
                    )}
                    {visible.map((entry) => (
                      <TimelineEvent key={entry.id} entry={entry} />
                    ))}
                  </div>
                )
              })()}
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ── Single event row ──────────────────────────────────────────

/** Extract agent name from timeline entry title (e.g. "AgentName → toolName") */
function extractAgentName(title: string): string | null {
  const arrowIdx = title.indexOf(' → ')
  if (arrowIdx > 0) return title.slice(0, arrowIdx).replace(/^[🗣️⚔️🧑‍⚖️📝🔄]\s*/, '').trim()
  // debate proposal: "🗣️ AgentName proposes ..."
  const proposeMatch = title.match(/^🗣️\s+(.+?)\s+proposes/)
  if (proposeMatch) return proposeMatch[1]
  // rebuttal: "⚔️ AgentName rebuts ..."
  const rebutMatch = title.match(/^⚔️\s+(.+?)\s+rebuts/)
  if (rebutMatch) return rebutMatch[1]
  return null
}

function TimelineEvent({ entry }: { entry: TimelineEntry }) {
  const [expanded, setExpanded] = useState(false)

  // Determine agent color from metadata or title
  const agentRole = (entry.metadata?.agent_role as string) || (entry.metadata?.fromRole as string) || (entry.metadata?.role as string)
  const agentName = extractAgentName(entry.title)
  const isToolOrDebate = [
    'tool_start', 'tool_done', 'debate_proposal', 'debate_rebuttal',
    'agent_thinking', 'agent_communication',
  ].includes(entry.type)
  const agentColors = isToolOrDebate ? getAgentColor(agentRole, agentName ?? undefined) : null

  return (
    <div
      className={cn(
        'flex items-start gap-2 pl-3 py-1.5 rounded-md transition-colors cursor-pointer',
        'border-l-2',
        // Use agent color for tool/debate events, status color for others
        agentColors
          ? agentColors.border
          : entry.status === 'active' ? 'border-l-blue-500 bg-blue-50/30 dark:bg-blue-900/10'
          : entry.status === 'done' ? 'border-l-gray-300 dark:border-l-gray-700'
          : entry.status === 'error' ? 'border-l-red-500 bg-red-50/20 dark:bg-red-900/10'
          : 'border-l-gray-200 dark:border-l-gray-700',
        'hover:bg-gray-50 dark:hover:bg-[#151528]',
      )}
      onClick={() => setExpanded((v) => !v)}
    >
      {/* Status dot */}
      <div className="mt-0.5">
        {statusIndicator(entry.status)}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-medium text-gray-800 dark:text-gray-200 truncate">
            {/* Color the agent name portion for tool events */}
            {agentColors && agentName ? (
              <>
                <span className={agentColors.text}>{agentName}</span>
                {entry.title.slice(entry.title.indexOf(agentName) + agentName.length)}
              </>
            ) : (
              entry.title
            )}
          </span>
          {/* Tool source badge for tool events */}
          {(entry.type === 'tool_start' || entry.type === 'tool_done') && (() => {
            const toolName = entry.title.includes(' → ') ? entry.title.split(' → ')[1] : ''
            if (!toolName) return null
            const src = getToolSource(toolName)
            if (src.source === 'core' || src.source === 'unknown') return null
            return (
              <span
                className={cn(
                  'inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[8px] font-medium border leading-none shrink-0',
                  src.bgColor, src.color,
                )}
                title={src.label}
              >
                {src.emoji} {src.label}
              </span>
            )
          })()}
          <span className="text-[9px] text-gray-400 dark:text-gray-500 shrink-0 ml-auto">
            {relativeTime(entry.timestamp)}
          </span>
        </div>

        {/* Compact description (always show) */}
        {entry.description && (
          <p className={cn(
            'text-[10px] text-gray-500 dark:text-gray-400 mt-0.5',
            !expanded && 'truncate',
            expanded && 'whitespace-pre-line',
          )}>
            {entry.description}
          </p>
        )}

        {/* Expanded metadata */}
        {expanded && entry.metadata && (
          <pre className="text-[9px] text-gray-400 dark:text-gray-500 mt-1 whitespace-pre-wrap break-all bg-gray-50 dark:bg-[#0d0d1a] rounded p-1.5">
            {JSON.stringify(entry.metadata, null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}
