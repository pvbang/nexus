// ============================================================
// MekongAI Nexus - ToolCallCard
// Inline chat card showing tool execution with expandable details
// Enhanced: full output rendering with format detection,
// copy button, and fullscreen modal via ToolOutputRenderer.
// Routes to specialized renderers for known tool types.
//
// Feature #2: Tool Trail Animation — Spinner + Elapsed Timer
// - OrbitalSpinner when running
// - Live elapsed timer with font-data + tabular-nums
// - Status transition animations (running → success/error)
// - Mini execution stages timeline
// - Auto-compact mode when > 3 tools running
// ============================================================

import { useState, useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Wrench,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  XCircle,
  Maximize2,
  X,
  Search,
  Globe,
  FileText,
  Terminal,
  Database,
  Zap,
  Hourglass,
  CircleCheck,
} from 'lucide-react'
import { cn } from '../../../lib/cn'
import { getAgentColor } from '../../../lib/constants'
import { Badge } from '../../ui/Badge'
import { ToolOutputRenderer } from '../../ui/ToolOutputRenderer'
import { useElapsedTimer } from '../../../hooks/useElapsedTimer'
import { useProcessStore } from '../../../stores/processStore'
import type { ToolExecution } from '../../../types/process'

// Specialized renderers
import {
  getToolRendererType,
  getToolSource,
  CodeExecutionRenderer,
  TodoRenderer,
  SearchResultsRenderer,
  UrlFetchRenderer,
  FileOperationRenderer,
  TaskDocRenderer,
} from './tool-renderers'

interface ToolCallCardProps {
  execution: ToolExecution
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function truncateJson(obj: Record<string, unknown>, maxLen = 200): string {
  const str = JSON.stringify(obj, null, 2)
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
}

/** Tool icon based on name */
function getToolIcon(name: string) {
  const n = (name || '').toLowerCase()
  if (n.includes('search') || n.includes('web')) return <Search className="h-4 w-4 text-amber-500 dark:text-amber-400" />
  if (n.includes('fetch') || n.includes('http') || n.includes('url') || n.includes('browser')) return <Globe className="h-4 w-4 text-amber-500 dark:text-amber-400" />
  if (n.includes('file') || n.includes('read') || n.includes('write') || n.includes('document')) return <FileText className="h-4 w-4 text-amber-500 dark:text-amber-400" />
  if (n.includes('code') || n.includes('execute') || n.includes('command') || n.includes('terminal')) return <Terminal className="h-4 w-4 text-amber-500 dark:text-amber-400" />
  if (n.includes('data') || n.includes('parse') || n.includes('json')) return <Database className="h-4 w-4 text-amber-500 dark:text-amber-400" />
  return <Wrench className="h-4 w-4 text-amber-500 dark:text-amber-400" />
}

// ── Orbital Spinner (SVG-based pulsing orbital) ───────────────

function OrbitalSpinner({ className }: { className?: string }) {
  return (
    <div className={cn('relative', className)}>
      <svg
        className="animate-orbital-spin h-4 w-4"
        viewBox="0 0 24 24"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        role="status"
        aria-label="Tool executing"
      >
        {/* Outer orbit ring */}
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="2"
          strokeDasharray="16 48"
          className="opacity-30"
        />
        {/* Inner active arc */}
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeDasharray="12 52"
          strokeLinecap="round"
          className="opacity-90"
        />
        {/* Orbital dot */}
        <circle
          cx="12"
          cy="2"
          r="2"
          fill="currentColor"
          className="opacity-80"
        />
      </svg>
    </div>
  )
}

// ── Status Transition Icon ────────────────────────────────────

type ToolStatus = ToolExecution['status']

function StatusTransitionIcon({ status }: { status: ToolStatus }) {
  const prevStatusRef = useRef<ToolStatus>(status)
  const [transitioning, setTransitioning] = useState(false)

  useEffect(() => {
    if (prevStatusRef.current === 'running' && status !== 'running') {
      setTransitioning(true)
      const timer = setTimeout(() => setTransitioning(false), 350)
      return () => clearTimeout(timer)
    }
    prevStatusRef.current = status
    return undefined
  }, [status])

  // Update ref after effect
  useEffect(() => {
    prevStatusRef.current = status
  }, [status])

  if (status === 'running') {
    return <OrbitalSpinner className="text-brand-500" />
  }

  if (status === 'completed') {
    return (
      <CheckCircle2
        className={cn(
          'h-4 w-4 text-emerald-500 dark:text-emerald-400',
          transitioning && 'animate-icon-fade-in',
        )}
      />
    )
  }

  if (status === 'failed') {
    return (
      <XCircle
        className={cn(
          'h-4 w-4 text-red-500 dark:text-red-400',
          transitioning && 'animate-icon-fade-in',
        )}
      />
    )
  }

  return null
}

// ── Elapsed Timer Display ─────────────────────────────────────

function ElapsedTimerDisplay({
  execution,
}: {
  execution: ToolExecution
}) {
  const isRunning = execution.status === 'running'

  const { elapsedFormatted } = useElapsedTimer({
    startTimestamp: execution.timestamp,
    isActive: isRunning,
    finalDurationMs: execution.duration,
  })

  // When completed and has server duration, show that
  if (!isRunning && execution.duration != null) {
    return (
      <Badge variant="default" size="sm">
        <span className="font-data tabular-nums">
          {formatDuration(execution.duration)}
        </span>
      </Badge>
    )
  }

  // When running, show live timer
  if (isRunning) {
    return (
      <span className="font-data tabular-nums text-micro text-brand-500 dark:text-brand-400 font-medium">
        {elapsedFormatted}
      </span>
    )
  }

  // Completed/failed without server duration — show elapsed snapshot
  return (
    <Badge variant="default" size="sm">
      <span className="font-data tabular-nums">
        {elapsedFormatted}
      </span>
    </Badge>
  )
}

// ── Tool Execution Stages Timeline ────────────────────────────

type ExecutionStage = 'calling' | 'executing' | 'done'

function getActiveStage(status: ToolStatus): ExecutionStage {
  switch (status) {
    case 'running':
      return 'executing'
    case 'completed':
    case 'failed':
      return 'done'
    default:
      return 'calling'
  }
}

const STAGES: Array<{ key: ExecutionStage; icon: typeof Zap; label: string }> = [
  { key: 'calling', icon: Zap, label: 'Calling' },
  { key: 'executing', icon: Hourglass, label: 'Executing' },
  { key: 'done', icon: CircleCheck, label: 'Done' },
]

function ToolStages({ status }: { status: ToolStatus }) {
  const activeStage = getActiveStage(status)
  const stageOrder: ExecutionStage[] = ['calling', 'executing', 'done']
  const activeIdx = stageOrder.indexOf(activeStage)

  return (
    <div className="flex items-center gap-1 mt-1.5">
      {STAGES.map((stage, idx) => {
        const isPast = idx < activeIdx
        const isCurrent = idx === activeIdx
        const isFailed = status === 'failed' && isCurrent
        const StageIcon = stage.icon

        return (
          <div key={stage.key} className="flex items-center gap-1">
            {idx > 0 && (
              <div
                className={cn(
                  'w-3 h-px',
                  isPast ? 'bg-emerald-400 dark:bg-emerald-500' : 'bg-gray-200 dark:bg-gray-700',
                )}
              />
            )}
            <div
              className={cn(
                'flex items-center gap-0.5 px-1 py-0.5 rounded text-micro transition-colors',
                isCurrent && !isFailed && 'text-brand-600 dark:text-brand-400 font-semibold',
                isCurrent && isFailed && 'text-red-500 dark:text-red-400 font-semibold',
                isPast && 'text-emerald-600 dark:text-emerald-400',
                !isPast && !isCurrent && 'text-gray-400 dark:text-gray-600',
              )}
            >
              <StageIcon className="h-2.5 w-2.5" />
              <span>{stage.label}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Tool Source Badge ─────────────────────────────────────────

function ToolSourceBadge({ toolName }: { toolName: string }) {
  const sourceInfo = getToolSource(toolName)
  // Don't show badge for generic 'core' tools to reduce noise
  if (sourceInfo.source === 'core' || sourceInfo.source === 'unknown') return null

  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-medium border leading-none',
        sourceInfo.bgColor,
        sourceInfo.color,
      )}
      title={sourceInfo.label}
    >
      <span className="text-[8px]">{sourceInfo.emoji}</span>
      {sourceInfo.label}
    </span>
  )
}

// ── Compact Tool Card (single-line when many tools) ───────────

function CompactToolCard({
  execution,
  onExpand,
}: {
  execution: ToolExecution
  onExpand: () => void
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className={cn(
        'flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs cursor-pointer',
        'hover:bg-gray-50 dark:hover:bg-[#22223a] transition-colors',
        'border-border dark:border-[#2a2a45]',
        'bg-white dark:bg-[#1a1a2e]',
        execution.status === 'running' && 'border-l-2 animate-border-pulse',
      )}
      onClick={onExpand}
    >
      {getToolIcon(execution.toolName)}
      <span className="font-mono font-medium text-text-primary dark:text-gray-100 truncate min-w-0">
        {execution.toolName}
      </span>
      <ToolSourceBadge toolName={execution.toolName} />
      <span className="flex-1" />
      <ElapsedTimerDisplay execution={execution} />
      <StatusTransitionIcon status={execution.status} />
    </motion.div>
  )
}

// ── Fullscreen output modal for ToolCallCard ──────────────────

function FullscreenOutputModal({
  execution,
  isFailed,
  onClose,
}: {
  execution: ToolExecution
  isFailed: boolean
  onClose: () => void
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      {/* Modal */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 8 }}
        transition={{ duration: 0.15 }}
        className={cn(
          'relative z-10 w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden',
          'bg-white dark:bg-[#0e0e20] border border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-100 dark:border-[#1a1a2e] shrink-0">
          {getToolIcon(execution.toolName)}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
                {execution.toolName}
              </h3>
              <ToolSourceBadge toolName={execution.toolName} />
            </div>
            <p className="text-[10px] text-gray-500 dark:text-gray-400">
              by <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>{execution.agentName}</span>
              {execution.duration != null && ` • ${formatDuration(execution.duration)}`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1a1a2e] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Args */}
          {Object.keys(execution.toolArgs).length > 0 && (
            <div>
              <span className="text-[9px] font-bold text-gray-400 uppercase mb-1 block">Input Arguments</span>
              <ToolOutputRenderer
                value={execution.toolArgs}
                maxHeight={200}
                compact
                showFullscreen={false}
              />
            </div>
          )}
          {/* Output */}
          <div>
            <span className={cn(
              'text-[9px] font-bold uppercase mb-1 block',
              isFailed ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400',
            )}>
              {isFailed ? 'Error' : 'Output / Result'}
            </span>
            <ToolOutputRenderer
              value={execution.error || execution.result}
              maxHeight={undefined}
              isError={isFailed}
              showFullscreen={false}
            />
          </div>
        </div>
      </motion.div>
    </div>
  )
}

// ── Main ToolCallCard (routes to specialized or generic) ──────

export function ToolCallCard({ execution }: ToolCallCardProps) {
  const rendererType = getToolRendererType(execution.toolName)

  switch (rendererType) {
    case 'code': return <CodeExecutionRenderer execution={execution} />
    case 'todo': return <TodoRenderer execution={execution} />
    case 'search': return <SearchResultsRenderer execution={execution} />
    case 'url': return <UrlFetchRenderer execution={execution} />
    case 'file': return <FileOperationRenderer execution={execution} />
    case 'task_doc': return <TaskDocRenderer execution={execution} />
    default: return <GenericToolCallCard execution={execution} />
  }
}

// ── Generic Tool Card (fallback for unrecognized tools) ───────

function GenericToolCallCard({ execution }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [showFullscreen, setShowFullscreen] = useState(false)
  const [forceExpanded, setForceExpanded] = useState(false)

  // Track previous status for flash animation
  const prevStatusRef = useRef<ToolStatus>(execution.status)
  const [flashClass, setFlashClass] = useState('')

  // Auto-compact: check how many tools are currently running in the process store
  const runningToolCount = useProcessStore(
    (s) => s.toolExecutions.filter((t) => t.status === 'running').length,
  )
  const isCompact = runningToolCount > 3 && execution.status === 'running' && !forceExpanded

  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'

  const hasOutput = !!(execution.result || execution.error)
  const outputStr = execution.error || execution.result || ''
  const isLongOutput = typeof outputStr === 'string' && outputStr.length > 100

  // Detect status transition for flash effect
  useEffect(() => {
    const prev = prevStatusRef.current
    if (prev === 'running' && execution.status === 'completed') {
      setFlashClass('animate-status-flash-success')
      const timer = setTimeout(() => setFlashClass(''), 600)
      prevStatusRef.current = execution.status
      return () => clearTimeout(timer)
    }
    if (prev === 'running' && execution.status === 'failed') {
      setFlashClass('animate-status-flash-error')
      const timer = setTimeout(() => setFlashClass(''), 600)
      prevStatusRef.current = execution.status
      return () => clearTimeout(timer)
    }
    prevStatusRef.current = execution.status
    return undefined
  }, [execution.status])

  // Compact mode — single line view
  if (isCompact) {
    return (
      <CompactToolCard
        execution={execution}
        onExpand={() => setForceExpanded(true)}
      />
    )
  }

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.97 }}
        animate={{
          opacity: 1,
          y: 0,
          scale: 1,
          boxShadow: isRunning
            ? '0 0 12px rgba(59, 130, 246, 0.15), 0 4px 12px rgba(0, 0, 0, 0.05)'
            : '0 1px 3px rgba(0, 0, 0, 0.04)',
        }}
        transition={{
          type: 'spring',
          stiffness: 380,
          damping: 28,
          mass: 0.7,
        }}
        className={cn(
          'rounded-xl border bg-white px-3.5 py-2.5 text-sm',
          'dark:bg-[#1a1a2e]',
          // Running state: pulsing left border accent with glow
          isRunning
            ? 'border-l-[3px] border-l-brand-500 border-t-border border-r-border border-b-border dark:border-t-[#2a2a45] dark:border-r-[#2a2a45] dark:border-b-[#2a2a45] animate-border-pulse'
            : 'border-border dark:border-[#2a2a45]',
          // Flash animation on status transition
          flashClass,
        )}
      >
        {/* Header row */}
        <div className="flex items-center gap-2">
          {/* Tool icon — orbital spinner when running, static when done */}
          {isRunning ? (
            <OrbitalSpinner className="text-brand-500" />
          ) : (
            getToolIcon(execution.toolName)
          )}

          <span className="font-mono font-medium text-text-primary dark:text-gray-100 text-xs">
            {execution.toolName}
          </span>
          <ToolSourceBadge toolName={execution.toolName} />
          <span className="text-xs text-text-muted dark:text-gray-500">
            by{' '}
            <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>
              {execution.agentName}
            </span>
          </span>

          {/* Right side: timer + status icon */}
          <span className="ml-auto flex items-center gap-1.5">
            <ElapsedTimerDisplay execution={execution} />
            <StatusTransitionIcon status={execution.status} />
          </span>
        </div>

        {/* Execution stages timeline */}
        <ToolStages status={execution.status} />

        {/* Compact args preview */}
        {Object.keys(execution.toolArgs).length > 0 && (
          <div className="mt-1.5 text-xs text-text-secondary dark:text-gray-400 font-mono whitespace-pre-wrap break-all">
            Args: {truncateJson(execution.toolArgs, 500)}
          </div>
        )}

        {/* Expandable result */}
        {hasOutput && (
          <div className="mt-1.5">
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setExpanded((p) => !p)}
                className="flex items-center gap-1 text-xs text-brand-600 dark:text-brand-400 hover:underline"
              >
                {expanded ? (
                  <>
                    <ChevronUp className="h-3 w-3" />
                    Hide result
                  </>
                ) : (
                  <>
                    <ChevronDown className="h-3 w-3" />
                    {isFailed ? 'Show error' : 'Show result'}
                  </>
                )}
              </button>
              {/* Fullscreen button for long output */}
              {isLongOutput && (
                <button
                  type="button"
                  onClick={() => setShowFullscreen(true)}
                  className="ml-auto p-1 rounded hover:bg-gray-100 dark:hover:bg-[#22223a] transition-colors text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  title="Xem đầy đủ"
                >
                  <Maximize2 className="h-3 w-3" />
                </button>
              )}
            </div>

            <AnimatePresence initial={false}>
              {expanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="mt-1.5">
                    <ToolOutputRenderer
                      value={execution.error || execution.result}
                      maxHeight={500}
                      compact
                      isError={isFailed}
                      showFullscreen
                      onFullscreen={() => setShowFullscreen(true)}
                    />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
      </motion.div>

      {/* Fullscreen modal */}
      <AnimatePresence>
        {showFullscreen && (
          <FullscreenOutputModal
            execution={execution}
            isFailed={isFailed}
            onClose={() => setShowFullscreen(false)}
          />
        )}
      </AnimatePresence>
    </>
  )
}
