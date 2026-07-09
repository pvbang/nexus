// ============================================================
// MekongAI Nexus - MessageEventExplorer
// Complete event explorer for historical messages.
// NOW USES SAME TimelineCard as ProcessStream for consistent UI.
// ============================================================

import React, { useState, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Timer,
  Wrench,
  Users,
  Brain,
  Search,
  MessageSquare,
  ChevronsDown,
  ChevronsUp,
  Filter,
  Download,
  AlertCircle,
  Loader2,
  Eye,
  EyeOff,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { useFullEvents } from '../../hooks/useFullEvents'
import { HistoricalDebateView } from './HistoricalDebateView'
import { TimelineCard } from '../process/TimelineCard'
import {
  convertExecutionEventsToTimeline,
  getTimelineItemCategory,
} from '../process/timeline-utils'
import type { TimelineItem, TimelineFilterCategory } from '../process/timeline-utils'
import type { ExecutionEvent } from '../../types/execution'

// ── Types ─────────────────────────────────────────────────────

/** Raw event from message metadata — loosely typed since backend schema varies */
type ProcessEvent = Record<string, unknown>

interface MessageEventExplorerProps {
  /** Raw execution events from message metadata (lightweight/truncated) */
  events: ProcessEvent[]
  /** Task ID for fetching full events from API */
  taskId?: string
  /** Session ID */
  sessionId: string
  /** Whether this is the currently active (live) task */
  isLive?: boolean
  /** Initial collapsed state (default: true for history) */
  defaultCollapsed?: boolean
}

type EventTypeFilter = 'all' | TimelineFilterCategory
// Pagination step size
const EVENTS_PAGE_SIZE = 20

interface EventSummary {
  toolCalls: number
  agentsSpawned: number
  thinkingEvents: number
  debateRounds: number
  analysisEvents: number
  stepEvents: number
  totalEvents: number
  errorEvents: number
  durationMs: number
  tools: string[]
  agents: string[]
}

// ── Constants ─────────────────────────────────────────────────

const RECENT_COUNT = 20

/** Event types to skip in display */
const SKIP_TYPES = new Set([
  'task_started', 'task_failed', 'task_cancelled',
  'heartbeat', 'pong', 'ping', 'token', 'done', 'error', 'usage',
])

// ── Accessors ─────────────────────────────────────────────────

function evtType(evt: ProcessEvent): string {
  return String(evt.type || evt.event_type || '')
}
function evtStr(evt: ProcessEvent, key: string): string {
  return String(evt[key] || '')
}
function evtNum(evt: ProcessEvent, key: string): number {
  return Number(evt[key] || 0)
}

// ── Helpers ───────────────────────────────────────────────────

function summarizeEvents(events: ProcessEvent[]): EventSummary {
  const tools = new Set<string>()
  const agents = new Set<string>()
  let toolCalls = 0
  let thinkingEvents = 0
  let debateRounds = 0
  let analysisEvents = 0
  let stepEvents = 0
  let errorEvents = 0
  let totalDuration = 0

  for (const evt of events) {
    const t = evtType(evt)
    if (t === 'tool_start' || t === 'tool_done') {
      if (t === 'tool_start') toolCalls++
      const tool = evtStr(evt, 'tool') || (evt.data && typeof evt.data === 'object' ? String((evt.data as Record<string, unknown>).tool || '') : '')
      if (tool) tools.add(tool)
      const dur = evtNum(evt, 'execution_time_ms')
      if (dur) totalDuration += dur
      if (evt.success === false) errorEvents++
    }
    if (t === 'agent_thinking' || t === 'thinking' || t === 'agent_response') {
      thinkingEvents++
    }
    if (t === 'agent_spawn') {
      const name = evtStr(evt, 'agent_name')
      if (name) agents.add(name)
    }
    if (t.startsWith('debate_') || t.startsWith('swarm_')) {
      debateRounds = Math.max(debateRounds, evtNum(evt, 'round'))
    }
    if (['deep_analysis', 'self_reflection', 'auto_verify', 'completion_check', 'mid_analysis'].includes(t)) {
      analysisEvents++
    }
    if (['step', 'planning_start', 'planning_done', 'react_phase'].includes(t)) {
      stepEvents++
    }
    const agentName = evtStr(evt, 'agent_name') || (evt.data && typeof evt.data === 'object' ? String((evt.data as Record<string, unknown>).agent_name || '') : '')
    if (agentName) agents.add(agentName)
  }

  return {
    toolCalls,
    agentsSpawned: agents.size,
    thinkingEvents,
    debateRounds,
    analysisEvents,
    stepEvents,
    totalEvents: events.length,
    errorEvents,
    durationMs: totalDuration,
    tools: Array.from(tools).slice(0, 8),
    agents: Array.from(agents).slice(0, 8),
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

// ── Filter Category Labels ────────────────────────────────────

const FILTER_LABELS: Record<EventTypeFilter, string> = {
  all: 'Tất cả',
  tool: 'Tool Calls',
  thinking: 'Thinking',
  debate: 'Debate',
  analysis: 'Analysis',
  step: 'Steps',
  other: 'Khác',
}

const FILTER_ICONS: Record<EventTypeFilter, React.ReactNode> = {
  all: null,
  tool: <Wrench className="h-3 w-3" />,
  thinking: <Brain className="h-3 w-3" />,
  debate: <MessageSquare className="h-3 w-3" />,
  analysis: <Search className="h-3 w-3" />,
  step: <CheckCircle2 className="h-3 w-3" />,
  other: null,
}

// ── Main Component ────────────────────────────────────────────

export function MessageEventExplorer({
  events,
  taskId,
  sessionId,
  isLive = false,
  defaultCollapsed = true,
}: MessageEventExplorerProps) {
  // ── State ───────────────────────────────────────────────────
  const [isExpanded, setIsExpanded] = useState(!defaultCollapsed || isLive)
  const [extraVisibleCount, setExtraVisibleCount] = useState(0)
  const [typeFilter, setTypeFilter] = useState<EventTypeFilter>('all')
  const [showFilterMenu, setShowFilterMenu] = useState(false)
  const [debateView, setDebateView] = useState(false)
  const [showAllItems, setShowAllItems] = useState(false)
  const filterRef = useRef<HTMLDivElement>(null)

  // ── Full events hook ────────────────────────────────────────
  const { fullEvents, isLoading, error, fetchFullEvents, isLoaded } = useFullEvents(taskId, sessionId)

  // ── Merge metadata events with full events ──────────────────
  const activeEvents: ProcessEvent[] = useMemo(() => {
    if (isLoaded && fullEvents && fullEvents.length > 0) {
      return fullEvents.map((fe) => ({
        ...fe.data,
        event_type: fe.event_type,
        type: fe.event_type,
        sequence: fe.sequence,
        created_at: fe.created_at,
        started_at: fe.started_at,
        id: fe.id,
        data: fe.data,
      }))
    }
    return events
  }, [events, fullEvents, isLoaded])

  // ── Filter out noise ────────────────────────────────────────
  const displayEvents = useMemo(() => {
    return activeEvents.filter((e) => {
      const t = evtType(e)
      if (SKIP_TYPES.has(t)) return false
      if (t === 'truncation_notice') return false
      return true
    })
  }, [activeEvents])

  // ── Convert to TimelineItems for rendering ──────────────────
  const timelineItems: TimelineItem[] = useMemo(() => {
    return convertExecutionEventsToTimeline(displayEvents as unknown as ExecutionEvent[])
  }, [displayEvents])

  // ── Apply type filter on timeline items ─────────────────────
  const filteredTimeline = useMemo(() => {
    if (typeFilter === 'all') return timelineItems
    return timelineItems.filter((item) => getTimelineItemCategory(item.kind) === typeFilter)
  }, [timelineItems, typeFilter])

  // ── Apply pagination (show RECENT_COUNT + extraVisibleCount) ──
  const maxVisible = RECENT_COUNT + extraVisibleCount
  const visibleTimeline = useMemo(() => {
    if (maxVisible >= filteredTimeline.length) {
      return filteredTimeline
    }
    return filteredTimeline.slice(-maxVisible)
  }, [filteredTimeline, maxVisible])
  const hiddenCount = filteredTimeline.length - visibleTimeline.length
  const canHide = extraVisibleCount > 0

  // ── Summary ─────────────────────────────────────────────────
  const summary = useMemo(() => summarizeEvents(displayEvents), [displayEvents])

  // ── Category counts for filter badges ───────────────────────
  const categoryCounts = useMemo(() => {
    const counts: Record<EventTypeFilter, number> = {
      all: timelineItems.length,
      tool: 0,
      thinking: 0,
      debate: 0,
      analysis: 0,
      step: 0,
      other: 0,
    }
    for (const item of timelineItems) {
      const cat = getTimelineItemCategory(item.kind)
      counts[cat]++
    }
    return counts
  }, [timelineItems])

  // ── Detect debate-heavy tasks ───────────────────────────────
  const hasDebate = categoryCounts.debate >= 3

  // ── Early return ────────────────────────────────────────────
  if (events.length === 0) return null

  return (
    <div className="mb-2">
      {/* ═══ Summary Bar (always visible) — SAME pattern as ProcessStream ═══ */}
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className={cn(
          'w-full flex items-center gap-2 px-3 py-4 rounded-lg text-left transition-all',
          'border text-sm',
          isExpanded
            ? 'bg-gray-50 dark:bg-[#12122a] border-gray-200 dark:border-[#2a2a45]'
            : 'bg-gray-50/50 dark:bg-[#12122a]/50 border-gray-100 dark:border-[#2a2a45]/50 hover:bg-gray-50 dark:hover:bg-[#12122a]',
        )}
      >
        {isExpanded ? (
          <ChevronDown className="h-3 w-3 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 text-gray-400 shrink-0" />
        )}

        {summary.errorEvents > 0 ? (
          <AlertCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
        )}

        <span className="text-text-secondary dark:text-gray-400 truncate font-bold">
          Agent Process
          {isLoaded && <span className="text-emerald-500 ml-1 text-[9px]">● full</span>}
        </span>

        <div className="flex items-center gap-1.5 ml-auto shrink-0">
          {summary.toolCalls > 0 && (
            <Badge variant="default" size="sm">
              <Wrench className="h-2.5 w-2.5 mr-0.5" />
              {summary.toolCalls}
            </Badge>
          )}
          {summary.agentsSpawned > 0 && (
            <Badge variant="default" size="sm">
              <Users className="h-2.5 w-2.5 mr-0.5" />
              {summary.agentsSpawned}
            </Badge>
          )}
          {summary.debateRounds > 0 && (
            <Badge variant="default" size="sm">
              🗣️ {summary.debateRounds}
            </Badge>
          )}
          {summary.errorEvents > 0 && (
            <Badge variant="default" size="sm" className="text-red-600 dark:text-red-400">
              ❌ {summary.errorEvents}
            </Badge>
          )}
          {summary.durationMs > 0 && (
            <span className="text-text-muted dark:text-gray-500 flex items-center gap-0.5">
              <Timer className="h-2.5 w-2.5" />
              {formatDuration(summary.durationMs)}
            </span>
          )}
          <span className="text-[10px] text-gray-400">
            {summary.totalEvents} events
          </span>
        </div>
      </button>

      {/* ═══ Expanded Panel ═══ */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            {/* ─── Control Bar ─── */}
            <div className="mt-2 flex items-center gap-2 flex-wrap text-[11px]">
              {/* Pagination info */}
              <div className="flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400">
                <span>Đang hiển thị {visibleTimeline.length}/{filteredTimeline.length}</span>
              </div>

              {/* Show/Hide Details toggle */}
              <button
                onClick={() => setShowAllItems((p) => !p)}
                className="flex items-center gap-1 px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 transition-colors"
                title={showAllItems ? "Thu gọn" : "Hiện chi tiết"}
              >
                {showAllItems ? (
                  <>
                    <EyeOff className="h-3 w-3" />
                    <ChevronsUp className="h-3 w-3" />
                  </>
                ) : (
                  <>
                    <Eye className="h-3 w-3" />
                    <ChevronsDown className="h-3 w-3" />
                  </>
                )}
              </button>

              {/* Filter dropdown */}
              <div className="relative" ref={filterRef}>
                <button
                  onClick={() => setShowFilterMenu(!showFilterMenu)}
                  className={cn(
                    'flex items-center gap-1 px-2 py-1 rounded-md border transition-colors',
                    typeFilter !== 'all'
                      ? 'border-blue-300 dark:border-blue-700 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                      : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400',
                  )}
                >
                  <Filter className="h-3 w-3" />
                  {typeFilter !== 'all' ? FILTER_LABELS[typeFilter] : 'Filter'}
                </button>
                {showFilterMenu && (
                  <div className="absolute top-full left-0 mt-1 z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1 min-w-[160px]">
                    {(Object.entries(FILTER_LABELS) as [EventTypeFilter, string][]).map(([key, label]) => (
                      <button
                        key={key}
                        onClick={() => { setTypeFilter(key); setShowFilterMenu(false) }}
                        className={cn(
                          'w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors',
                          typeFilter === key
                            ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                            : 'hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300',
                        )}
                      >
                        {FILTER_ICONS[key]}
                        <span>{label}</span>
                        <span className="ml-auto text-[9px] text-gray-400">{categoryCounts[key]}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Load full events button */}
              {!isLoaded && taskId && (
                <button
                  onClick={fetchFullEvents}
                  disabled={isLoading}
                  className="flex items-center gap-1 px-2 py-1 rounded-md border border-emerald-200 dark:border-emerald-700 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 transition-colors disabled:opacity-50"
                >
                  {isLoading ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Download className="h-3 w-3" />
                  )}
                  Tải chi tiết đầy đủ
                </button>
              )}

              {/* Error indicator */}
              {error && (
                <span className="text-[10px] text-red-500 flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  {error}
                </span>
              )}

              {/* Full data indicator */}
              {isLoaded && (
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <CheckCircle2 className="h-3 w-3" />
                  Đã tải đầy đủ
                </span>
              )}
            </div>

            {/* ─── Debate/Events tab toggle ─── */}
            {hasDebate && (
              <div className="mt-2 flex gap-1 mb-2">
                <button
                  onClick={() => setDebateView(false)}
                  className={cn(
                    'px-3 py-1.5 rounded-md text-[11px] font-medium transition-colors',
                    !debateView
                      ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300'
                      : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700',
                  )}
                >
                  📋 Events ({timelineItems.length})
                </button>
                <button
                  onClick={() => setDebateView(true)}
                  className={cn(
                    'px-3 py-1.5 rounded-md text-[11px] font-medium transition-colors',
                    debateView
                      ? 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300'
                      : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700',
                  )}
                >
                  💬 Debate ({categoryCounts.debate} messages)
                </button>
              </div>
            )}

            {/* ─── Debate Chat View ─── */}
            {debateView && hasDebate ? (
              <HistoricalDebateView events={displayEvents} />
            ) : (
            /* ─── Timeline Cards — SAME cards as ProcessStream ─── */
            <div className="mt-2 space-y-2 max-h-[70vh] overflow-y-auto pr-1">
              {/* Show more / Show all older events */}
              {hiddenCount > 0 && (
                <div className="w-full flex items-center justify-center gap-2 text-[10px] py-1.5 border border-dashed border-blue-200 dark:border-blue-800 rounded-md bg-blue-50/30 dark:bg-blue-900/10">
                  <button
                    onClick={() => setExtraVisibleCount((c) => c + EVENTS_PAGE_SIZE)}
                    className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-200 transition-colors"
                  >
                    ⬆️ Show {Math.min(EVENTS_PAGE_SIZE, hiddenCount)} older
                  </button>
                  {hiddenCount > EVENTS_PAGE_SIZE && (
                    <>
                      <span className="text-gray-300 dark:text-gray-600">|</span>
                      <button
                        onClick={() => setExtraVisibleCount(filteredTimeline.length)}
                        className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-200 transition-colors"
                      >
                        ⬆️ Show all {hiddenCount}
                      </button>
                    </>
                  )}
                </div>
              )}
              {/* Hide / Collapse events */}
              {canHide && (
                <div className="w-full flex items-center justify-center gap-2 text-[10px] py-1.5 border border-dashed border-orange-200 dark:border-orange-800 rounded-md bg-orange-50/30 dark:bg-orange-900/10">
                  <button
                    onClick={() => setExtraVisibleCount((c) => Math.max(0, c - EVENTS_PAGE_SIZE))}
                    className="text-orange-600 dark:text-orange-400 hover:text-orange-800 dark:hover:text-orange-200 transition-colors"
                  >
                    ⬇️ Hide {EVENTS_PAGE_SIZE} events
                  </button>
                  <span className="text-gray-300 dark:text-gray-600">|</span>
                  <button
                    onClick={() => setExtraVisibleCount(0)}
                    className="text-orange-600 dark:text-orange-400 hover:text-orange-800 dark:hover:text-orange-200 transition-colors"
                  >
                    ⬇️ Hide all extra
                  </button>
                </div>
              )}

              {/* Timeline cards — using the SAME TimelineCard as ProcessStream */}
              {visibleTimeline.map((item, idx) => (
                <motion.div
                  key={`${item.kind}-${item.timestamp}-${idx}`}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.15, delay: Math.min(idx * 0.03, 0.3) }}
                >
                  <TimelineCard item={item} />
                </motion.div>
              ))}

              {/* Empty state */}
              {visibleTimeline.length === 0 && (
                <div className="text-center text-gray-400 text-[11px] py-4">
                  Không có events phù hợp với filter hiện tại.
                </div>
              )}
            </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
