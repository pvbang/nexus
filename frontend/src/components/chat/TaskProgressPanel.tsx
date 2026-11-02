// ============================================================
// Nexus - Task Progress Panel (Right sidebar)
// v2: Newest first, expandable detail, JSON/Markdown renderer
// ============================================================

import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  ChevronRight, ListTodo, Bot, Clock, CheckCircle,
  AlertCircle, Loader2, ExternalLink, Square, Activity,
  RefreshCw, FileText, Code2, Play,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { apiGet, apiPost, type Task, type Agent } from '../../api'
import { usePreferenceStore } from '../../stores/preferenceStore'
import { useProcessStore } from '../../stores/processStore'
import { ProcessViewer } from '../process/ProcessViewer'

type PanelTab = 'process' | 'tasks'

interface TaskProgressPanelProps {
  sessionId: string
}

// ── Helpers ───────────────────────────────────────────────────

const ACTIVE_STATUSES = new Set(['executing', 'planning', 'decomposing'])

const statusMeta = (status: string) => {
  switch (status) {
    case 'executing':
    case 'decomposing':
    case 'planning':
      return { icon: <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin" />, label: 'text-blue-600', dot: 'bg-blue-400 animate-pulse' }
    case 'completed':
      return { icon: <CheckCircle className="w-3.5 h-3.5 text-emerald-500" />, label: 'text-emerald-700', dot: 'bg-emerald-400' }
    case 'failed':
      return { icon: <AlertCircle className="w-3.5 h-3.5 text-red-500" />, label: 'text-red-600', dot: 'bg-red-400' }
    case 'cancelled':
      return { icon: <Square className="w-3.5 h-3.5 text-gray-400" />, label: 'text-gray-500', dot: 'bg-gray-300' }
    default:
      return { icon: <Clock className="w-3.5 h-3.5 text-gray-400" />, label: 'text-gray-500', dot: 'bg-gray-300' }
  }
}

// ── JSON / Markdown smart renderer ──────────────────────────

function SmartContent({ value }: { value: unknown }) {
  const [mode, setMode] = useState<'pretty' | 'raw'>('pretty')

  if (value === null || value === undefined || value === '') {
    return <span className="text-gray-400 italic text-[11px]">(empty)</span>
  }

  const isStr = typeof value === 'string'

  // Try to parse JSON string
  let parsed: unknown = value
  let isJson = false
  if (isStr) {
    try {
      parsed = JSON.parse(value as string)
      isJson = true
    } catch {
      isJson = false
    }
  } else if (typeof value === 'object') {
    isJson = true
    parsed = value
  }

  const rawStr = isStr ? (value as string) : JSON.stringify(value, null, 2)
  const prettyJson = isJson ? JSON.stringify(parsed, null, 2) : null
  const isMarkdown = isStr && !isJson && (
    (value as string).includes('\n#') ||
    (value as string).includes('\n-') ||
    (value as string).includes('\n*') ||
    (value as string).includes('**') ||
    (value as string).includes('```')
  )

  if (isJson && prettyJson) {
    return (
      <div>
        <div className="flex gap-1.5 mb-1.5">
          {(['pretty', 'raw'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={cn(
                'px-2 py-0.5 rounded text-[9px] font-semibold transition-colors',
                mode === m ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
              )}
            >
              {m === 'pretty' ? <span className="flex items-center gap-1"><FileText className="w-2.5 h-2.5" /> JSON</span>
                : <span className="flex items-center gap-1"><Code2 className="w-2.5 h-2.5" /> Raw</span>}
            </button>
          ))}
        </div>
        <pre className="text-[10px] font-mono text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900 rounded-[6px] p-2.5 border border-gray-200 dark:border-gray-700 overflow-x-auto whitespace-pre-wrap leading-relaxed max-h-[300px] overflow-y-auto">
          {mode === 'pretty' ? prettyJson : rawStr}
        </pre>
      </div>
    )
  }

  if (isMarkdown) {
    return (
      <div className="text-[11px] text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-wrap max-h-[300px] overflow-y-auto bg-gray-50 dark:bg-gray-900 rounded-[6px] p-2.5 border border-gray-200 dark:border-gray-700">
        {rawStr}
      </div>
    )
  }

  return (
    <pre className="text-[10px] font-mono text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900 rounded-[6px] p-2.5 border border-gray-200 dark:border-gray-700 overflow-x-auto whitespace-pre-wrap leading-relaxed max-h-[300px] overflow-y-auto">
      {rawStr}
    </pre>
  )
}

// ── Task Detail Modal / Drawer ───────────────────────────────

function TaskDetailPanel({ task, onClose }: { task: Task; onClose: () => void }) {
  const result = task.result as Record<string, unknown> | undefined
  const description = task.description
  const created = task.created_at ? new Date(task.created_at).toLocaleString('vi-VN') : null
  const updated = task.updated_at ? new Date(task.updated_at).toLocaleString('vi-VN') : null

  return (
    <div className="absolute inset-0 z-20 bg-white dark:bg-[#0d0d1a] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-gray-200 dark:border-[#2a2a45] bg-gray-50 dark:bg-[#13132a]">
        <button
          onClick={onClose}
          className="w-7 h-7 rounded-[7px] hover:bg-gray-200 dark:hover:bg-gray-700 flex items-center justify-center transition-colors"
        >
          <ChevronRight className="w-4 h-4 rotate-180 text-gray-500" />
        </button>
        <div className="flex-1 min-w-0">
          <p className="text-[12px] font-semibold text-gray-800 dark:text-gray-100 truncate">{task.title}</p>
          <p className="text-[10px] text-gray-400 capitalize">{task.status}</p>
        </div>
        {statusMeta(task.status).icon}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Meta */}
        <div className="grid grid-cols-2 gap-2 text-[10px] text-gray-500">
          {created && <div><span className="font-semibold">Created:</span> {created}</div>}
          {updated && <div><span className="font-semibold">Updated:</span> {updated}</div>}
          <div className="col-span-2">
            <span className="font-semibold">ID:</span>{' '}
            <span className="font-mono text-gray-400">{task.id.slice(0, 12)}…</span>
          </div>
        </div>

        {/* Description */}
        {description && (
          <section>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Description</p>
            <SmartContent value={description} />
          </section>
        )}

        {/* Result */}
        {result && (
          <section>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Result</p>
            {Object.keys(result).length === 0 ? (
              <span className="text-[11px] text-gray-400 italic">Empty result</span>
            ) : (
              <div className="space-y-2">
                {Object.entries(result).map(([key, val]) => (
                  <div key={key}>
                    <p className="text-[10px] font-medium text-gray-500 mb-0.5">{key}</p>
                    <SmartContent value={val} />
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {/* Error */}
        {task.error && (
          <section>
            <p className="text-[10px] font-semibold text-red-400 uppercase tracking-wider mb-1.5">Error</p>
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-[6px] p-2.5">
              <pre className="text-[10px] text-red-600 dark:text-red-400 whitespace-pre-wrap font-mono">{task.error}</pre>
            </div>
          </section>
        )}

        {/* Full task detail link */}
        <Link
          to={`/tasks/${task.id}`}
          className="flex items-center gap-1.5 text-[11px] text-blue-600 hover:text-blue-700 transition-colors"
        >
          <ExternalLink className="w-3.5 h-3.5" />
          View full task detail
        </Link>
      </div>
    </div>
  )
}

// ── Task Row ─────────────────────────────────────────────────

function TaskRow({
  task,
  onCancel,
  onResume,
  onSelect,
}: {
  task: Task
  onCancel: (id: string) => void
  onResume: (id: string) => void
  onSelect: (task: Task) => void
}) {
  const { icon } = statusMeta(task.status)
  const isActive = ACTIVE_STATUSES.has(task.status)
  const timeAgo = task.updated_at
    ? (() => {
        const s = Math.floor((Date.now() - new Date(task.updated_at).getTime()) / 1000)
        if (s < 60) return `${s}s ago`
        if (s < 3600) return `${Math.floor(s / 60)}m ago`
        return `${Math.floor(s / 3600)}h ago`
      })()
    : null

  return (
    <div
      className={cn(
        'flex items-start gap-2 p-2 rounded-[8px] cursor-pointer transition-all group',
        isActive
          ? 'bg-blue-50/80 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800'
          : task.status === 'completed'
          ? 'bg-emerald-50/60 dark:bg-emerald-900/10 border border-emerald-100 dark:border-emerald-900 hover:border-emerald-300'
          : task.status === 'failed'
          ? 'bg-red-50/60 dark:bg-red-900/10 border border-red-100 dark:border-red-900 hover:border-red-300'
          : 'bg-gray-50 dark:bg-gray-800/40 border border-gray-100 dark:border-gray-700/50 hover:border-gray-300'
      )}
      onClick={() => onSelect(task)}
    >
      <div className="mt-0.5 shrink-0">{icon}</div>
      <div className="flex-1 min-w-0">
        <p className="text-[12px] font-medium text-gray-800 dark:text-gray-100 line-clamp-2 leading-snug group-hover:text-blue-700 dark:group-hover:text-blue-300 transition-colors">
          {task.title}
        </p>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className={cn('text-[10px] capitalize font-medium', statusMeta(task.status).label)}>
            {task.status}
          </span>
          {timeAgo && <span className="text-[10px] text-gray-400">· {timeAgo}</span>}
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        {isActive && (
          <button
            onClick={(e) => { e.stopPropagation(); onCancel(task.id) }}
            className="w-5 h-5 rounded flex items-center justify-center text-red-400 hover:text-red-600 hover:bg-red-50 transition-colors"
            title="Cancel task"
          >
            <Square className="w-3 h-3" fill="currentColor" />
          </button>
        )}
        {(task.status === 'cancelled' || task.status === 'failed') && (
          <button
            onClick={(e) => { e.stopPropagation(); onResume(task.id) }}
            className="w-5 h-5 rounded flex items-center justify-center text-green-500 hover:text-green-700 hover:bg-green-50 dark:hover:bg-green-900/30 transition-colors"
            title="Continue task"
          >
            <Play className="w-3 h-3" fill="currentColor" />
          </button>
        )}
        <ChevronRight className="w-3.5 h-3.5 text-gray-300 group-hover:text-gray-500 transition-colors" />
      </div>
    </div>
  )
}

// ── Main Component ───────────────────────────────────────────

export function TaskProgressPanel({ sessionId }: TaskProgressPanelProps) {
  const [allTasks, setAllTasks] = useState<Task[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const toggleRightPanel = usePreferenceStore((s) => s.toggleRightPanel)

  // Process state
  const processStage = useProcessStore((s) => s.stage)
  const hasActiveProcess = processStage !== 'idle' && processStage !== 'completed'
  const [activeTab, setActiveTab] = useState<PanelTab>('tasks')

  useEffect(() => {
    if (hasActiveProcess) setActiveTab('process')
    else setActiveTab('tasks')
  }, [hasActiveProcess])

  const loadData = useCallback(async () => {
    try {
      const [sessionTasksData, globalTasksData, agentsData] = await Promise.all([
        apiGet<Task[]>(`/tasks/?limit=20&session_id=${sessionId}`),
        apiGet<Task[]>('/tasks/?limit=10'),
        apiGet<Agent[]>('/agents/?limit=10'),
      ])

      // Merge, de-dup, sort newest first
      const merged = new Map<string, Task>()
      ;[...sessionTasksData, ...globalTasksData].forEach((t) => merged.set(t.id, t))
      const sorted = Array.from(merged.values()).sort((a, b) => {
        const ta = new Date(a.updated_at || a.created_at || 0).getTime()
        const tb = new Date(b.updated_at || b.created_at || 0).getTime()
        return tb - ta
      })

      setAllTasks(sorted)
      setAgents(agentsData)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 5000)
    return () => clearInterval(interval)
  }, [loadData])

  const handleCancel = async (taskId: string) => {
    try {
      await apiPost(`/tasks/${taskId}/cancel`, {})
      loadData()
    } catch { /* silent */ }
  }

  const handleResume = async (taskId: string) => {
    try {
      await apiPost(`/tasks/${taskId}/resume`, {})
      loadData()
    } catch { /* silent */ }
  }

  const activeTasks = allTasks.filter((t) => ACTIVE_STATUSES.has(t.status))
  const activeAgents = agents.filter((a) => ['active', 'busy'].includes(a.status))

  return (
    <div className="w-[300px] border-l border-border-light bg-white dark:bg-[#0d0d1a] dark:border-[#2a2a45] flex flex-col shrink-0 overflow-hidden relative">
      {/* Detail overlay */}
      {selectedTask && (
        <TaskDetailPanel task={selectedTask} onClose={() => setSelectedTask(null)} />
      )}

      {/* Header */}
      <div className="px-3 py-2.5 border-b border-border-light dark:border-[#2a2a45] flex items-center justify-between">
        <div className="flex items-center gap-1">
          {hasActiveProcess && (
            <button
              type="button"
              onClick={() => setActiveTab('process')}
              className={cn(
                'flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold transition-colors',
                activeTab === 'process'
                  ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                  : 'text-text-muted hover:bg-[#f4f5f7] dark:hover:bg-gray-800',
              )}
            >
              <Activity className="w-3 h-3" />
              Process
            </button>
          )}
          <button
            type="button"
            onClick={() => setActiveTab('tasks')}
            className={cn(
              'flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold transition-colors',
              activeTab === 'tasks'
                ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                : 'text-text-muted hover:bg-[#f4f5f7] dark:hover:bg-gray-800',
            )}
          >
            <ListTodo className="w-3 h-3" />
            Tasks
            {activeTasks.length > 0 && (
              <span className="ml-0.5 min-w-[16px] h-4 px-1 bg-blue-500 text-white text-[9px] font-bold rounded-full flex items-center justify-center">
                {activeTasks.length}
              </span>
            )}
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={loadData}
            className="w-6 h-6 rounded-[6px] hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center justify-center transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-3.5 h-3.5 text-gray-400" />
          </button>
          <button
            onClick={toggleRightPanel}
            className="w-7 h-7 rounded-[7px] hover:bg-[#f4f5f7] dark:hover:bg-gray-800 flex items-center justify-center transition-colors"
            title="Close panel (Ctrl+.)"
          >
            <ChevronRight className="w-4 h-4 text-text-muted" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Live Process tab */}
        {activeTab === 'process' && hasActiveProcess && (
          <div className="p-3">
            <ProcessViewer />
          </div>
        )}

        {/* Tasks tab */}
        {activeTab === 'tasks' && (
          loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
            </div>
          ) : (
            <div className="p-3 space-y-4">
              {/* Active Agents strip */}
              {activeAgents.length > 0 && (
                <section>
                  <div className="flex items-center gap-1.5 px-1 mb-2">
                    <Bot className="w-3.5 h-3.5 text-purple-500" />
                    <span className="text-[10px] font-semibold text-text-muted uppercase tracking-wider">
                      Active Agents
                    </span>
                    <span className="ml-auto text-[10px] bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 px-1.5 py-0.5 rounded-full font-medium">
                      {activeAgents.length}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {activeAgents.map((agent) => (
                      <span
                        key={agent.id}
                        className="flex items-center gap-1 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-100 dark:border-purple-800 rounded-full text-[10px] font-medium text-purple-700 dark:text-purple-300"
                      >
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                        {agent.name}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {/* All Tasks — newest first */}
              <section>
                <div className="flex items-center gap-1.5 px-1 mb-2">
                  <ListTodo className="w-3.5 h-3.5 text-text-muted" />
                  <span className="text-[10px] font-semibold text-text-muted uppercase tracking-wider">
                    All Tasks
                  </span>
                  <span className="ml-auto text-[10px] text-gray-400">
                    {allTasks.length} total
                  </span>
                </div>

                {allTasks.length === 0 ? (
                  <div className="text-center py-8 text-gray-400">
                    <ListTodo className="w-6 h-6 mx-auto mb-2 opacity-30" />
                    <p className="text-[12px]">No tasks yet</p>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {allTasks.map((task) => (
                      <TaskRow
                        key={task.id}
                        task={task}
                        onCancel={handleCancel}
                        onResume={handleResume}
                        onSelect={setSelectedTask}
                      />
                    ))}
                  </div>
                )}
              </section>

              {/* Quick Links */}
              <section className="pt-2 border-t border-border-light dark:border-[#2a2a45]">
                <div className="space-y-1">
                  <Link to="/tasks" className="flex items-center gap-2 px-2 py-1.5 rounded-[8px] text-[11px] text-text-secondary hover:bg-[#f4f5f7] dark:hover:bg-gray-800 hover:text-text-dark transition-colors">
                    <ListTodo className="w-3.5 h-3.5" /> View all tasks
                  </Link>
                  <Link to="/agents" className="flex items-center gap-2 px-2 py-1.5 rounded-[8px] text-[11px] text-text-secondary hover:bg-[#f4f5f7] dark:hover:bg-gray-800 hover:text-text-dark transition-colors">
                    <Bot className="w-3.5 h-3.5" /> View all agents
                  </Link>
                </div>
              </section>
            </div>
          )
        )}
      </div>
    </div>
  )
}
