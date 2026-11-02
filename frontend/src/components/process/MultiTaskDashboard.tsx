// ============================================================
// Nexus - MultiTaskDashboard
// Dashboard for multiple concurrent tasks with tabs/accordion
// ============================================================

import { useState, useEffect, useMemo, useCallback } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ListTodo,
  ArrowUpDown,
  ExternalLink,
  RefreshCw,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { apiGet } from '../../api'
import { useRealtimeStore, type LiveTaskStatus } from '../../stores/realtimeStore'
import { useProcessStore } from '../../stores/processStore'
import { useNotificationStore } from '../../stores/notificationStore'
import { usePolling } from '../../hooks/usePolling'
import { POLLING_INTERVALS } from '../../lib/constants'

// Components
import { ProcessViewer } from './ProcessViewer'
import { TaskFlowGraph } from './TaskFlowGraph'
import { TaskTab, type TaskTabData } from './TaskTab'
import { Card } from '../ui/Card'
import { Badge } from '../ui/Badge'
import { Tabs, type Tab } from '../ui/Tabs'
import { Spinner } from '../ui/Spinner'
import { EmptyState } from '../ui/EmptyState'

import type { Task } from '../../types/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SortKey = 'created' | 'status' | 'progress'

interface MultiTaskDashboardProps {
  className?: string
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VIEW_TABS: Tab[] = [
  { id: 'process', label: 'Process' },
  { id: 'graph', label: 'Graph' },
  { id: 'events', label: 'Events' },
]

const STATUS_PRIORITY: Record<string, number> = {
  executing: 0,
  running: 0,
  planning: 1,
  decomposing: 1,
  pending: 2,
  queued: 2,
  completed: 3,
  done: 3,
  failed: 4,
  cancelled: 5,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isTaskActive(status: string): boolean {
  return ['executing', 'running', 'planning', 'decomposing'].includes(status)
}

function taskToTabData(
  task: Task,
  liveStatus: LiveTaskStatus | undefined,
  agentCount: number,
): TaskTabData {
  const status = liveStatus?.status ?? task.status
  const progress = liveStatus?.progress ?? (status === 'completed' || status === 'done' ? 100 : 0)
  const createdAt = task.created_at ? new Date(task.created_at).getTime() : Date.now()
  const duration = Math.floor((Date.now() - createdAt) / 1000)

  return {
    id: task.id,
    title: liveStatus?.title ?? task.title,
    status,
    progress,
    agentCount,
    duration,
    stage: status,
  }
}

function sortTasks(tasks: TaskTabData[], sortKey: SortKey): TaskTabData[] {
  const sorted = [...tasks]
  switch (sortKey) {
    case 'status':
      sorted.sort((a, b) => (STATUS_PRIORITY[a.status] ?? 9) - (STATUS_PRIORITY[b.status] ?? 9))
      break
    case 'progress':
      sorted.sort((a, b) => b.progress - a.progress)
      break
    case 'created':
    default:
      sorted.sort((a, b) => b.duration - a.duration) // older first (longer duration)
      break
  }
  return sorted
}

// ---------------------------------------------------------------------------
// TaskEventsReplay lazy import wrapper
// ---------------------------------------------------------------------------

function EventsPanel({ taskId }: { taskId: string }) {
  // Dynamic import to avoid circular dependency
  const [Component, setComponent] = useState<React.ComponentType<{ taskId: string }> | null>(null)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    import('../tasks/TaskEventsReplay')
      .then((mod) => setComponent(() => mod.TaskEventsReplay))
      .catch(() => setLoadError(true))
  }, [])

  if (loadError) {
    return (
      <EmptyState
        icon="⚠️"
        title="Events unavailable"
        description="Could not load events replay component."
      />
    )
  }

  if (!Component) {
    return (
      <div className="flex justify-center py-8">
        <Spinner size="md" className="text-brand-500" />
      </div>
    )
  }

  return <Component taskId={taskId} />
}

// ---------------------------------------------------------------------------
// Notification tracker hook
// ---------------------------------------------------------------------------

function useCompletionNotifier(tasks: TaskTabData[]) {
  const addNotification = useNotificationStore((s) => s.add)
  const prevStatuses = useMemo(() => new Map<string, string>(), [])

  useEffect(() => {
    for (const task of tasks) {
      const prev = prevStatuses.get(task.id)
      if (prev && prev !== task.status) {
        if (task.status === 'completed' || task.status === 'done') {
          addNotification('success', 'Task completed', `"${task.title}" has finished.`)
        } else if (task.status === 'failed') {
          addNotification('error', 'Task failed', `"${task.title}" encountered an error.`)
        }
      }
      prevStatuses.set(task.id, task.status)
    }
  }, [tasks, prevStatuses, addNotification])
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function MultiTaskDashboard({ className }: MultiTaskDashboardProps) {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [activeViewTab, setActiveViewTab] = useState('process')
  const [sortKey, setSortKey] = useState<SortKey>('status')

  // Live task statuses from WebSocket
  const taskStatuses = useRealtimeStore((s) => s.taskStatuses)
  const agentStatuses = useRealtimeStore((s) => s.agentStatuses)

  // Current active process
  const processTaskId = useProcessStore((s) => s.taskId)

  // Poll for executing tasks from API
  const { data: apiTasks, loading, refresh } = usePolling<Task[]>({
    fetcher: () => apiGet<Task[]>('/tasks/?status=executing'),
    interval: POLLING_INTERVALS.fast,
    enabled: true,
  })

  // Merge live + API tasks
  const allTasks = useMemo<TaskTabData[]>(() => {
    const taskMap = new Map<string, TaskTabData>()

    // API tasks
    if (apiTasks) {
      for (const task of apiTasks) {
        const live = taskStatuses[task.id]
        const agentCount = Object.values(agentStatuses).filter(
          (a) => a.status === 'active' || a.status === 'running',
        ).length
        taskMap.set(task.id, taskToTabData(task, live, agentCount))
      }
    }

    // Live task statuses (may include tasks not in API response)
    for (const [id, live] of Object.entries(taskStatuses)) {
      if (!taskMap.has(id)) {
        taskMap.set(id, {
          id,
          title: live.title || `Task ${id.slice(0, 8)}`,
          status: live.status,
          progress: live.progress ?? 0,
          agentCount: 0,
          duration: Math.floor((Date.now() - live.timestamp) / 1000),
          stage: live.status,
        })
      }
    }

    // Include current process task
    if (processTaskId && !taskMap.has(processTaskId)) {
      const live = taskStatuses[processTaskId]
      taskMap.set(processTaskId, {
        id: processTaskId,
        title: live?.title ?? `Task ${processTaskId.slice(0, 8)}`,
        status: live?.status ?? 'executing',
        progress: live?.progress ?? 0,
        agentCount: 0,
        duration: 0,
        stage: live?.status ?? 'executing',
      })
    }

    return sortTasks(Array.from(taskMap.values()), sortKey)
  }, [apiTasks, taskStatuses, agentStatuses, processTaskId, sortKey])

  // Auto-select first task
  useEffect(() => {
    if (allTasks.length > 0 && !selectedTaskId) {
      setSelectedTaskId(allTasks[0].id)
    }
    // If selected task was removed, reset
    if (selectedTaskId && !allTasks.find((t) => t.id === selectedTaskId)) {
      setSelectedTaskId(allTasks[0]?.id ?? null)
    }
  }, [allTasks, selectedTaskId])

  // Track completions
  useCompletionNotifier(allTasks)

  const selectedTask = useMemo(
    () => allTasks.find((t) => t.id === selectedTaskId) ?? null,
    [allTasks, selectedTaskId],
  )

  const activeCount = useMemo(
    () => allTasks.filter((t) => isTaskActive(t.status)).length,
    [allTasks],
  )

  const handleSortCycle = useCallback(() => {
    setSortKey((prev) => {
      if (prev === 'status') return 'progress'
      if (prev === 'progress') return 'created'
      return 'status'
    })
  }, [])

  const handleNavigateToTask = useCallback((taskId: string) => {
    window.location.hash = `#/tasks/${taskId}`
  }, [])

  // --- Empty state ---
  if (!loading && allTasks.length === 0) {
    return (
      <Card variant="default" padding="none" className={cn('flex flex-col', className)}>
        <EmptyState
          icon="📋"
          title="No active tasks"
          description="Tasks will appear here when they are created and executing."
          action={{ label: 'Refresh', onClick: refresh }}
        />
      </Card>
    )
  }

  return (
    <Card variant="default" padding="none" className={cn('flex flex-col h-full overflow-hidden', className)}>
      {/* ── Header ─────────────────────────────────── */}
      <div
        className={cn(
          'flex items-center gap-2 px-4 py-2.5 border-b shrink-0',
          'border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        <ListTodo className="h-4 w-4 text-brand-500 dark:text-brand-400" aria-hidden />
        <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
          Multi-Task Dashboard
        </h2>

        {activeCount > 0 && (
          <Badge variant="brand" size="sm" dot>
            {activeCount} active
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {/* Sort */}
          <button
            type="button"
            onClick={handleSortCycle}
            className={cn(
              'flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium transition-colors',
              'text-gray-500 hover:text-gray-700 hover:bg-gray-100',
              'dark:text-gray-400 dark:hover:text-gray-200 dark:hover:bg-[#2a2a45]',
            )}
            title={`Sort by: ${sortKey}`}
          >
            <ArrowUpDown className="h-3 w-3" aria-hidden />
            {sortKey}
          </button>

          {/* Refresh */}
          <button
            type="button"
            onClick={refresh}
            className={cn(
              'p-1.5 rounded transition-colors',
              'text-gray-400 hover:text-gray-600 hover:bg-gray-100',
              'dark:text-gray-500 dark:hover:text-gray-300 dark:hover:bg-[#2a2a45]',
            )}
            aria-label="Refresh tasks"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* ── Task tabs (horizontal scroll) ──────────── */}
      <div
        className={cn(
          'flex gap-2 overflow-x-auto px-3 py-2 border-b shrink-0',
          'border-gray-200 dark:border-[#2a2a45]',
          'scrollbar-thin scrollbar-thumb-gray-300 dark:scrollbar-thumb-gray-600',
        )}
      >
        <AnimatePresence mode="popLayout">
          {allTasks.map((task) => (
            <motion.div
              key={task.id}
              layout
              initial={{ opacity: 0, scale: 0.9, x: -20 }}
              animate={{ opacity: 1, scale: 1, x: 0 }}
              exit={{ opacity: 0, scale: 0.9, x: -20 }}
              transition={{ duration: 0.2 }}
            >
              <TaskTab
                task={task}
                active={task.id === selectedTaskId}
                onClick={() => setSelectedTaskId(task.id)}
              />
            </motion.div>
          ))}
        </AnimatePresence>

        {loading && allTasks.length === 0 && (
          <div className="flex items-center gap-2 px-3 py-2">
            <Spinner size="sm" className="text-brand-500" />
            <span className="text-xs text-gray-500 dark:text-gray-400">Loading tasks…</span>
          </div>
        )}
      </div>

      {/* ── Selected task content ──────────────────── */}
      {selectedTask ? (
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          {/* Task info bar */}
          <div
            className={cn(
              'flex items-center gap-3 px-4 py-2 border-b shrink-0',
              'border-gray-100 dark:border-[#1f1f3a]',
            )}
          >
            <h3 className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate flex-1">
              {selectedTask.title}
            </h3>
            <Badge
              variant={
                selectedTask.status === 'completed' || selectedTask.status === 'done'
                  ? 'success'
                  : selectedTask.status === 'failed'
                    ? 'error'
                    : isTaskActive(selectedTask.status)
                      ? 'info'
                      : 'default'
              }
              size="sm"
              dot={isTaskActive(selectedTask.status)}
            >
              {selectedTask.status}
            </Badge>
            <button
              type="button"
              onClick={() => handleNavigateToTask(selectedTask.id)}
              className={cn(
                'p-1 rounded transition-colors',
                'text-gray-400 hover:text-brand-600 hover:bg-brand-50',
                'dark:text-gray-500 dark:hover:text-brand-400 dark:hover:bg-brand-900/20',
              )}
              title="Open task detail"
              aria-label="Open task detail page"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* View tabs */}
          <div className="px-4 pt-2 shrink-0">
            <Tabs
              tabs={VIEW_TABS}
              activeTab={activeViewTab}
              onChange={setActiveViewTab}
              variant="pill"
              size="sm"
            />
          </div>

          {/* Tab content */}
          <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
            <AnimatePresence mode="wait">
              <motion.div
                key={`${selectedTask.id}-${activeViewTab}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.15 }}
              >
                {activeViewTab === 'process' && <ProcessViewer />}
                {activeViewTab === 'graph' && (
                  <TaskFlowGraph taskId={selectedTask.id} />
                )}
                {activeViewTab === 'events' && (
                  <EventsPanel taskId={selectedTask.id} />
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center">
          <EmptyState
            icon="👈"
            title="Select a task"
            description="Choose a task from the tabs above to view its details."
          />
        </div>
      )}
    </Card>
  )
}
