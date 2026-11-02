// ============================================================
// Nexus - Enhanced TaskDetail Component
// Task detail page with tabs: Overview, Events, Subtasks, Graph
// ============================================================

import { useState, useMemo, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { cn } from '../../lib/cn'
import { apiPost, getTaskDetail } from '../../api'
import { usePolling } from '../../hooks/usePolling'
import { useRealtimeStore } from '../../stores/realtimeStore'
import { formatDate, formatRelativeTime } from '../../lib/utils'
import { POLLING_INTERVALS } from '../../lib/constants'
import { Card } from '../ui/Card'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Tabs, type Tab } from '../ui/Tabs'
import { Skeleton } from '../ui/Skeleton'
import { EmptyState } from '../ui/EmptyState'
import { ProgressBar } from '../ui/ProgressBar'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import { ErrorBoundary } from '../ui/ErrorBoundary'
import { TaskEventsReplay } from './TaskEventsReplay'
import { SubtaskTree } from './SubtaskTree'
import { KnowledgeGraph } from '../KnowledgeGraph'
import type { Task } from '../../types/api'

// ---------------------------------------------------------------------------
// Debate types (loose shape from backend)
// ---------------------------------------------------------------------------

interface DebateProposal {
  agent: string
  role?: string
  content: string
  tools_used?: string[]
}

interface DebateRebuttal {
  agent: string
  content: string
}

interface DebateVerdict {
  verdict?: string
  synthesis?: string
  reasoning?: string
  winner?: string | null
}

interface DebateRound {
  round: number
  proposals?: DebateProposal[]
  rebuttals?: DebateRebuttal[]
  verdict?: DebateVerdict
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<string, {
  label: string
  icon: string
  variant: 'default' | 'info' | 'success' | 'error' | 'warning'
}> = {
  pending: { label: 'Pending', icon: '⏳', variant: 'default' },
  decomposing: { label: 'Decomposing', icon: '🔬', variant: 'warning' },
  planning: { label: 'Planning', icon: '📋', variant: 'info' },
  executing: { label: 'Executing', icon: '⚡', variant: 'info' },
  completed: { label: 'Completed', icon: '✅', variant: 'success' },
  failed: { label: 'Failed', icon: '❌', variant: 'error' },
  cancelled: { label: 'Cancelled', icon: '🚫', variant: 'error' },
  cancelling: { label: 'Cancelling…', icon: '⏹️', variant: 'warning' },
  suspended: { label: 'Suspended', icon: '⏸️', variant: 'warning' },
  crashed: { label: 'Crashed', icon: '💥', variant: 'error' },
}

const TAB_DEFS: Tab[] = [
  { id: 'overview', label: 'Overview', icon: <OverviewIcon /> },
  { id: 'events', label: 'Events', icon: <EventsIcon /> },
  { id: 'subtasks', label: 'Subtasks', icon: <SubtasksIcon /> },
  { id: 'graph', label: 'Graph', icon: <GraphIcon /> },
]

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function OverviewIcon() {
  return (
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6z" />
    </svg>
  )
}

function EventsIcon() {
  return (
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function SubtasksIcon() {
  return (
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" />
    </svg>
  )
}

function GraphIcon() {
  return (
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 14.25v2.25m3-4.5v4.5m3-6.75v6.75m3-9v9M6 20.25h12A2.25 2.25 0 0020.25 18V6A2.25 2.25 0 0018 3.75H6A2.25 2.25 0 003.75 6v12A2.25 2.25 0 006 20.25z" />
    </svg>
  )
}

function BackArrowIcon() {
  return (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusConfig(status: string) {
  return STATUS_CONFIG[status] ?? { label: status, icon: '❓', variant: 'default' as const }
}

function isTaskActive(status: string): boolean {
  return ['executing', 'running', 'planning', 'decomposing', 'cancelling'].includes(status)
}

/**
 * Extract markdown content from a task result.
 * The backend may return:
 *  - A plain string
 *  - A JSON string like '{"content": "# markdown...", "tool_calls": 8}'
 *  - An object with `content`, `final_answer`, or other fields
 */
function extractResultContent(result: unknown): string | null {
  if (!result) return null

  // Already a string — try to parse as JSON first
  if (typeof result === 'string') {
    const trimmed = result.trim()
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (typeof parsed === 'object' && parsed !== null) {
          if (typeof parsed.content === 'string') return parsed.content
          if (typeof parsed.final_answer === 'string') return parsed.final_answer
          if (typeof parsed.result === 'string') return parsed.result
        }
      } catch {
        // Not valid JSON, use as-is
      }
    }
    return trimmed
  }

  // Object — extract known fields
  if (typeof result === 'object' && result !== null) {
    const obj = result as Record<string, unknown>
    if (typeof obj.content === 'string') return obj.content
    if (typeof obj.final_answer === 'string') return obj.final_answer
    if (typeof obj.result === 'string') return obj.result
    // Fallback: stringify
    return JSON.stringify(result, null, 2)
  }

  return String(result)
}

/**
 * Lightweight per-tab error boundary wrapper
 */
function TabErrorFallback({ tabName, error }: { tabName: string; error: string }) {
  return (
    <EmptyState
      icon="⚠️"
      title={`${tabName} tab encountered an error`}
      description={error}
    />
  )
}

function renderPriorityStars(priority: number) {
  const stars = Math.min(priority, 5)
  return (
    <div className="flex items-center gap-0.5">
      {Array.from({ length: 5 }).map((_, i) => (
        <span
          key={i}
          className={cn(
            'text-xs',
            i < stars ? 'text-amber-500' : 'text-gray-200 dark:text-gray-600',
          )}
        >
          ★
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DetailSkeleton() {
  return (
    <div className="max-w-[900px] mx-auto px-4 sm:px-6 py-6 space-y-6">
      <Skeleton className="h-4 w-24" />
      <div className="flex gap-4">
        <Skeleton variant="rectangular" className="h-12 w-12 rounded-xl" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-4 w-1/3" />
        </div>
      </div>
      <Skeleton variant="rectangular" className="h-10 w-64" />
      <Skeleton variant="rectangular" className="h-48 w-full rounded-xl" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Overview Tab
// ---------------------------------------------------------------------------

function OverviewTab({ task }: { task: Task }) {
  const result = task.result
  const resultObj = result as Record<string, unknown> | undefined
  const hasDebate = resultObj &&
    resultObj.strategy === 'debate' &&
    Array.isArray(resultObj.all_rounds)

  const renderedContent = useMemo(() => extractResultContent(result), [result])

  return (
    <div className="space-y-5">
      {/* Description */}
      {task.description && (
        <Card variant="default" padding="md">
          <h3 className={cn(
            'text-xs font-semibold uppercase tracking-wider mb-2',
            'text-gray-500 dark:text-gray-400',
          )}>
            Description
          </h3>
          <p className="text-sm text-gray-800 dark:text-gray-200 leading-relaxed whitespace-pre-wrap">
            {task.description}
          </p>
        </Card>
      )}

      {/* Result */}
      {renderedContent && (
        <Card
          variant="default"
          padding="md"
          className="border-emerald-200 dark:border-emerald-800/50 bg-emerald-50/50 dark:bg-emerald-900/10"
        >
          <h3 className={cn(
            'text-xs font-semibold uppercase tracking-wider mb-3 flex items-center gap-1.5',
            'text-emerald-700 dark:text-emerald-400',
          )}>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Final Result
          </h3>
          <MarkdownRenderer
            content={renderedContent}
            className="text-emerald-900 dark:text-emerald-200"
          />

          {/* Debate rounds */}
          {hasDebate && (
            <DebateRoundsView rounds={resultObj!.all_rounds as DebateRound[]} />
          )}
        </Card>
      )}

      {/* Error */}
      {task.error && (
        <Card
          variant="default"
          padding="md"
          className="border-red-200 dark:border-red-800/50 bg-red-50/50 dark:bg-red-900/10"
        >
          <h3 className={cn(
            'text-xs font-semibold uppercase tracking-wider mb-2 flex items-center gap-1.5',
            'text-red-700 dark:text-red-400',
          )}>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
            Error
          </h3>
          <p className="text-sm text-red-800 dark:text-red-300 font-mono whitespace-pre-wrap">
            {task.error}
          </p>
        </Card>
      )}

      {/* No content */}
      {!task.description && !result && !task.error && (
        <EmptyState
          icon="📄"
          title="No overview data"
          description="This task has no description, result, or error information."
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Debate Rounds (within overview)
// ---------------------------------------------------------------------------

function DebateRoundsView({ rounds }: { rounds: DebateRound[] }) {
  return (
    <div className="mt-6 border-t border-emerald-200 dark:border-emerald-800/50 pt-6">
      <h4 className="text-sm font-bold text-emerald-800 dark:text-emerald-300 mb-4 flex items-center gap-2">
        💬 Debate Logs
      </h4>
      <div className="space-y-4">
        {rounds.map((round, i) => (
          <Card key={i} variant="default" padding="sm" className="bg-white dark:bg-[#1a1a2e]">
            <h5 className="text-xs font-bold text-emerald-700 dark:text-emerald-400 uppercase mb-3 border-b border-emerald-50 dark:border-emerald-900/30 pb-2">
              Round {round.round}
            </h5>

            {/* Proposals */}
            {round.proposals && round.proposals.length > 0 && (
              <div className="mb-3">
                <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider mb-2">Proposals</div>
                <div className="space-y-2">
                  {round.proposals.map((p, idx) => (
                    <div key={idx} className="rounded-lg p-3 bg-gray-50 dark:bg-[#0f0f23] border border-gray-100 dark:border-gray-800">
                      <div className="flex items-center gap-2 mb-1">
                        <strong className="text-xs text-gray-800 dark:text-gray-200">{p.agent}</strong>
                        {p.role ? (
                          <Badge variant="info" size="sm">{p.role}</Badge>
                        ) : null}
                      </div>
                      <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed whitespace-pre-wrap">
                        {p.content}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Rebuttals */}
            {round.rebuttals && round.rebuttals.length > 0 && (
              <div className="mb-3">
                <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider mb-2">Rebuttals</div>
                <div className="space-y-2">
                  {round.rebuttals.map((r, idx) => (
                    <div key={idx} className="rounded-lg p-3 bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-800/50">
                      <div className="font-semibold text-xs text-amber-800 dark:text-amber-400 mb-1">
                        {r.agent}
                      </div>
                      <p className="text-xs text-amber-900 dark:text-amber-300 leading-relaxed whitespace-pre-wrap">
                        {r.content}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Verdict */}
            {round.verdict && (
              <div className="rounded-lg p-3 bg-purple-50 dark:bg-purple-900/10 border border-purple-100 dark:border-purple-800/50 mt-3">
                <div className="flex items-center gap-1.5 text-[11px] font-bold text-purple-800 dark:text-purple-400 uppercase tracking-wider mb-2">
                  ⚖️ Judge Verdict
                </div>
                <p className="text-xs text-purple-900 dark:text-purple-300 font-medium whitespace-pre-wrap">
                  {round.verdict.synthesis || round.verdict.reasoning || round.verdict.verdict || ''}
                </p>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// TaskDetail
// ---------------------------------------------------------------------------

export function TaskDetail() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('overview')
  const [cancelling, setCancelling] = useState(false)
  const [isGraphExpanded, setIsGraphExpanded] = useState(false)

  const liveStatus = useRealtimeStore((s) =>
    taskId ? s.taskStatuses[taskId] : undefined,
  )

  const active = liveStatus
    ? isTaskActive(liveStatus.status)
    : false

  const { data: task, loading, error, refresh } = usePolling<Task>({
    fetcher: () => getTaskDetail(taskId!),
    interval: active ? POLLING_INTERVALS.fast : POLLING_INTERVALS.slow,
    enabled: !!taskId,
  })

  // Merge live status
  const mergedTask = useMemo<Task | null>(() => {
    if (!task) return null
    if (liveStatus) {
      return { ...task, status: liveStatus.status, title: liveStatus.title || task.title }
    }
    return task
  }, [task, liveStatus])

  const handleCancel = useCallback(async () => {
    if (!taskId) return
    setCancelling(true)
    try {
      await apiPost(`/tasks/${taskId}/cancel`, {})
      refresh()
    } catch {
      // Will refresh anyway
    } finally {
      setCancelling(false)
    }
  }, [taskId, refresh])

  // --- Loading ---
  if (loading && !task) {
    return <DetailSkeleton />
  }

  // --- Error ---
  if (error && !task) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <EmptyState
          icon="⚠️"
          title="Task not found"
          description={error || 'The requested task could not be loaded.'}
          action={{ label: 'Back to Tasks', onClick: () => navigate('/tasks') }}
        />
      </div>
    )
  }

  if (!mergedTask) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <EmptyState
          icon="🔍"
          title="Task not found"
          description="No task data available."
          action={{ label: 'Back to Tasks', onClick: () => navigate('/tasks') }}
        />
      </div>
    )
  }

  const cfg = getStatusConfig(mergedTask.status)
  const isActive = isTaskActive(mergedTask.status)

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[900px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Back link */}
        <Link
          to="/tasks"
          className={cn(
            'inline-flex items-center gap-1.5 text-xs transition-colors',
            'text-gray-500 hover:text-gray-800',
            'dark:text-gray-400 dark:hover:text-gray-200',
          )}
        >
          <BackArrowIcon />
          Back to Tasks
        </Link>

        {/* Header */}
        <div className="flex items-start gap-4">
          <div
            className={cn(
              'w-12 h-12 rounded-xl flex items-center justify-center text-xl shrink-0',
              cfg.variant === 'success' && 'bg-emerald-50 dark:bg-emerald-900/20',
              cfg.variant === 'error' && 'bg-red-50 dark:bg-red-900/20',
              cfg.variant === 'info' && 'bg-blue-50 dark:bg-blue-900/20',
              cfg.variant === 'warning' && 'bg-amber-50 dark:bg-amber-900/20',
              cfg.variant === 'default' && 'bg-gray-100 dark:bg-gray-800',
            )}
          >
            {cfg.icon}
          </div>

          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100 leading-tight">
              {mergedTask.title}
            </h1>
            <div className="flex flex-wrap items-center gap-3 mt-2">
              <Badge variant={cfg.variant} size="md" dot={isActive}>
                {cfg.label}
              </Badge>

              {mergedTask.created_at && (
                <span className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  {formatDate(mergedTask.created_at)}
                </span>
              )}

              {/* Priority */}
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                  Priority
                </span>
                {renderPriorityStars(mergedTask.priority)}
              </div>
            </div>
          </div>

          {/* View Artifacts link */}
          <Link
            to={`/artifacts/${taskId}`}
            className={cn(
              'inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors shrink-0',
              'bg-gray-100 text-gray-700 hover:bg-gray-200',
              'dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700',
            )}
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.06-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
            </svg>
            Files
          </Link>

          {/* Cancel button */}
          {isActive && (
            <Button
              variant="danger"
              size="sm"
              loading={cancelling}
              onClick={handleCancel}
              icon={
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              }
            >
              Cancel
            </Button>
          )}
        </div>

        {/* Live progress bar */}
        {isActive && liveStatus?.progress !== undefined && (
          <ProgressBar
            value={liveStatus.progress}
            variant="default"
            size="sm"
            animated
            showValue
            label="Task progress"
          />
        )}

        {/* Tabs */}
        <Tabs
          tabs={TAB_DEFS}
          activeTab={activeTab}
          onChange={setActiveTab}
          variant="underline"
          className="w-full"
        />

        {/* Tab content */}
        <div role="tabpanel" id={`tabpanel-${activeTab}`}>
          {activeTab === 'overview' && (
            <ErrorBoundary fallback={<TabErrorFallback tabName="Overview" error="Failed to render overview" />}>
              <OverviewTab task={mergedTask} />
            </ErrorBoundary>
          )}

          {activeTab === 'events' && taskId && (
            <ErrorBoundary fallback={<TabErrorFallback tabName="Events" error="Failed to load events timeline" />}>
              <TaskEventsReplay taskId={taskId} />
            </ErrorBoundary>
          )}

          {activeTab === 'subtasks' && (
            <ErrorBoundary fallback={<TabErrorFallback tabName="Subtasks" error="Failed to render subtask tree" />}>
              <SubtaskTree task={mergedTask} />
            </ErrorBoundary>
          )}

          {activeTab === 'graph' && (
            <ErrorBoundary fallback={<TabErrorFallback tabName="Graph" error="Failed to render knowledge graph" />}>
              <div
                className={cn(
                  'transition-all duration-300',
                  isGraphExpanded
                    ? 'fixed inset-4 z-50 bg-white dark:bg-[#1a1a2e] shadow-2xl rounded-xl flex flex-col p-5'
                    : 'relative h-[500px] flex flex-col',
                )}
              >
                <div className="flex items-center justify-between mb-4 shrink-0">
                  <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Graph Data Visualisation
                  </h3>
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    onClick={() => setIsGraphExpanded(!isGraphExpanded)}
                    icon={
                      isGraphExpanded ? (
                        <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" />
                        </svg>
                      ) : (
                        <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
                        </svg>
                      )
                    }
                    aria-label={isGraphExpanded ? 'Minimize' : 'Expand'}
                    title={isGraphExpanded ? 'Minimize' : 'Expand'}
                  />
                </div>
                <div className={cn(
                  'flex-1 min-h-0 border rounded-lg overflow-hidden relative',
                  'border-gray-200 dark:border-[#2a2a45]',
                )}>
                  <KnowledgeGraph />
                </div>
              </div>
            </ErrorBoundary>
          )}
        </div>

        {/* Metadata footer */}
        <div className={cn(
          'text-[11px] space-y-1 border-t pt-4',
          'text-gray-400 border-gray-200',
          'dark:text-gray-500 dark:border-[#2a2a45]',
        )}>
          <p>
            <span className="font-medium">Task ID:</span> {mergedTask.id}
          </p>
          <p>
            <span className="font-medium">Priority:</span> {mergedTask.priority}/10
          </p>
          {mergedTask.updated_at && (
            <p>
              <span className="font-medium">Updated:</span>{' '}
              {formatDate(mergedTask.updated_at)} ({formatRelativeTime(mergedTask.updated_at)})
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
