// ============================================================
// MekongAI Nexus - Enhanced TaskManager Component
// Task list with filters, search, sort, report generator modal
// ============================================================

import { useState, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { cn } from '../lib/cn'
import { apiGet, apiPost } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useRealtimeStore } from '../stores/realtimeStore'
import { formatRelativeTime } from '../lib/utils'
import { POLLING_INTERVALS } from '../lib/constants'
import { Card } from './ui/Card'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { Input } from './ui/Input'
import { Select } from './ui/Select'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import { Modal } from './ui/Modal'
import { ReportGenerator } from './tasks/ReportGenerator'
import type { Task } from '../types/api'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<string, {
  label: string
  icon: string
  variant: 'default' | 'info' | 'success' | 'error' | 'warning' | 'purple'
}> = {
  pending: { label: 'Pending', icon: '⏳', variant: 'default' },
  decomposing: { label: 'Decomposing', icon: '🔬', variant: 'warning' },
  planning: { label: 'Planning', icon: '📋', variant: 'info' },
  executing: { label: 'Executing', icon: '⚡', variant: 'info' },
  completed: { label: 'Completed', icon: '✅', variant: 'success' },
  failed: { label: 'Failed', icon: '❌', variant: 'error' },
  cancelled: { label: 'Cancelled', icon: '🚫', variant: 'error' },
}

const FILTER_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'executing', label: 'Executing' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
]

const SORT_OPTIONS = [
  { value: 'date-desc', label: 'Newest first' },
  { value: 'date-asc', label: 'Oldest first' },
  { value: 'priority-desc', label: 'Priority ↓' },
  { value: 'priority-asc', label: 'Priority ↑' },
  { value: 'status', label: 'Status' },
]

const STATUS_ORDER: Record<string, number> = {
  executing: 0,
  planning: 1,
  decomposing: 2,
  pending: 3,
  completed: 4,
  failed: 5,
  cancelled: 6,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusConfig(status: string) {
  return STATUS_CONFIG[status] ?? { label: status, icon: '❓', variant: 'default' as const }
}

function isTaskActive(status: string): boolean {
  return ['executing', 'running', 'planning', 'decomposing'].includes(status)
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
// TaskManager
// ---------------------------------------------------------------------------

export function TaskManager() {
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('date-desc')
  const [reportModalOpen, setReportModalOpen] = useState(false)
  const [cancellingId, setCancellingId] = useState<string | null>(null)

  const taskStatuses = useRealtimeStore((s) => s.taskStatuses)

  const fetchPath = filter ? `/tasks/?status=${filter}&limit=100` : '/tasks/?limit=100'

  const { data: tasks, loading, error, refresh } = usePolling<Task[]>({
    fetcher: () => apiGet<Task[]>(fetchPath),
    interval: POLLING_INTERVALS.normal,
    enabled: true,
  })

  // Merge realtime status into tasks
  const mergedTasks = useMemo(() => {
    if (!tasks) return []
    return tasks.map((task) => {
      const live = taskStatuses[task.id]
      if (live) {
        return { ...task, status: live.status, title: live.title || task.title }
      }
      return task
    })
  }, [tasks, taskStatuses])

  // Filter + search + sort
  const displayTasks = useMemo(() => {
    let result = mergedTasks

    // Search filter
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (t) =>
          t.title.toLowerCase().includes(q) ||
          t.description?.toLowerCase().includes(q) ||
          t.id.toLowerCase().includes(q),
      )
    }

    // Sort
    result = [...result].sort((a, b) => {
      switch (sort) {
        case 'date-asc':
          return (a.created_at ?? '').localeCompare(b.created_at ?? '')
        case 'priority-desc':
          return b.priority - a.priority
        case 'priority-asc':
          return a.priority - b.priority
        case 'status':
          return (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99)
        case 'date-desc':
        default:
          return (b.created_at ?? '').localeCompare(a.created_at ?? '')
      }
    })

    return result
  }, [mergedTasks, search, sort])

  const handleCancel = useCallback(async (taskId: string) => {
    setCancellingId(taskId)
    try {
      await apiPost(`/tasks/${taskId}/cancel`, {})
      refresh()
    } catch {
      // Silently handle - will refresh anyway
    } finally {
      setCancellingId(null)
    }
  }, [refresh])

  const navigate = useNavigate()

  const handleNavigateToTask = useCallback((taskId: string) => {
    navigate(`/tasks/${taskId}`)
  }, [navigate])

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className={cn(
        'border-b border-border',
        'bg-surface-base dark:bg-surface-raised dark:border-border-subtle',
      )}>
        <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Tasks
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                {displayTasks.length} task{displayTasks.length !== 1 ? 's' : ''}
                {filter && ` (filtered: ${filter})`}
              </p>
            </div>

            <Button
              variant="primary"
              size="sm"
              onClick={() => setReportModalOpen(true)}
              icon={
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
              }
            >
              Generate Report
            </Button>
          </div>

          {/* Filter bar */}
          <div className="mt-4 flex flex-col sm:flex-row items-start sm:items-center gap-3">
            {/* Status pills */}
            <div className={cn(
              'flex gap-1 rounded-lg p-1',
              'bg-gray-100 dark:bg-surface-base',
            )}>
              {FILTER_OPTIONS.map((f) => (
                <button
                  key={f.value}
                  onClick={() => setFilter(f.value)}
                  className={cn(
                    'px-3 py-1.5 text-xs font-medium rounded-md transition-all duration-150',
                    filter === f.value
                      ? 'bg-white text-gray-900 shadow-subtle dark:bg-surface-tertiary dark:text-white'
                      : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>

            {/* Search */}
            <div className="flex-1 w-full sm:w-auto sm:max-w-xs">
              <Input
                variant="search"
                placeholder="Search tasks..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                clearable
                onClear={() => setSearch('')}
              />
            </div>

            {/* Sort */}
            <div className="w-full sm:w-40">
              <Select
                options={SORT_OPTIONS}
                value={sort}
                onChange={setSort}
                size="sm"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Task List */}
      <div className={cn(
        'flex-1 overflow-y-auto',
        'bg-gray-50 dark:bg-surface-base',
      )}>
        <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6">
          {/* Loading */}
          {loading && !tasks && (
            <div className="flex items-center justify-center py-16">
              <Spinner size="md" className="text-blue-500" />
            </div>
          )}

          {/* Error */}
          {error && !tasks && (
            <EmptyState
              icon="⚠️"
              title="Failed to load tasks"
              description={error}
              action={{ label: 'Retry', onClick: refresh }}
            />
          )}

          {/* Empty */}
          {!loading && tasks && displayTasks.length === 0 && (
            <EmptyState
              icon="📋"
              title={search ? 'No matching tasks' : 'No tasks yet'}
              description={
                search
                  ? `No tasks match "${search}". Try a different search.`
                  : 'Send a request via Chat to start generating tasks.'
              }
              action={
                search
                  ? { label: 'Clear search', onClick: () => setSearch('') }
                  : undefined
              }
            />
          )}

          {/* Task cards */}
          <div className="space-y-3">
            {displayTasks.map((task, index) => {
              const cfg = getStatusConfig(task.status)
              const active = isTaskActive(task.status)

              return (
                <Card
                  key={task.id}
                  variant="interactive"
                  padding="none"
                  className={cn(
                    'animate-in fade-in slide-in-from-bottom-1 duration-200',
                  )}
                  style={{ animationDelay: `${index * 40}ms` } as React.CSSProperties}
                  onClick={() => handleNavigateToTask(task.id)}
                >
                  <div className="p-4">
                    <div className="flex items-start gap-4">
                      {/* Status icon */}
                      <div
                        className={cn(
                          'w-10 h-10 rounded-lg flex items-center justify-center text-lg shrink-0',
                          active && 'animate-pulse',
                          cfg.variant === 'success' && 'bg-emerald-50 dark:bg-emerald-900/20',
                          cfg.variant === 'error' && 'bg-red-50 dark:bg-red-900/20',
                          cfg.variant === 'info' && 'bg-blue-50 dark:bg-blue-900/20',
                          cfg.variant === 'warning' && 'bg-amber-50 dark:bg-amber-900/20',
                          cfg.variant === 'default' && 'bg-gray-100 dark:bg-gray-800',
                        )}
                      >
                        {cfg.icon}
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-3">
                          <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 leading-snug truncate">
                            {task.title}
                          </h3>
                          <Badge variant={cfg.variant} size="sm" dot={active}>
                            {cfg.label}
                          </Badge>
                        </div>

                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2">
                          {/* Priority */}
                          <div className="flex items-center gap-1.5">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                              Priority
                            </span>
                            {renderPriorityStars(task.priority)}
                          </div>

                          {/* Created */}
                          {task.created_at && (
                            <span className="text-xs text-gray-500 dark:text-gray-400">
                              {formatRelativeTime(task.created_at)}
                            </span>
                          )}

                          {/* Task ID */}
                          <span className="text-[10px] text-gray-400 dark:text-gray-500 font-mono">
                            {task.id.slice(0, 8)}
                          </span>
                        </div>

                        {/* Error display */}
                        {task.error && (
                          <div className={cn(
                            'mt-3 p-2.5 rounded-lg text-xs',
                            'bg-red-50 border border-red-200 text-red-700',
                            'dark:bg-red-900/20 dark:border-red-800 dark:text-red-400',
                          )}>
                            <span className="font-medium">⚠️ Error: </span>
                            {task.error.length > 120 ? task.error.slice(0, 120) + '…' : task.error}
                          </div>
                        )}
                      </div>

                      {/* Cancel button for active tasks */}
                      {active && (
                        <Button
                          variant="ghost"
                          size="sm"
                          iconOnly
                          loading={cancellingId === task.id}
                          onClick={(e) => {
                            e.stopPropagation()
                            handleCancel(task.id)
                          }}
                          icon={
                            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                          }
                          aria-label="Cancel task"
                          title="Cancel task"
                        />
                      )}
                    </div>
                  </div>
                </Card>
              )
            })}
          </div>
        </div>
      </div>

      {/* Report Generator Modal */}
      <Modal
        open={reportModalOpen}
        onClose={() => setReportModalOpen(false)}
        title="Generate Report"
        size="md"
      >
        <ReportGenerator />
      </Modal>
    </div>
  )
}
