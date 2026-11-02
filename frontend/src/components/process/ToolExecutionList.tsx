// ============================================================
// Nexus - ToolExecutionList
// Full-content display with expandable detail + popup modal
// Enhanced: uses ToolOutputRenderer for smart format detection,
// syntax highlighting, copy button, and fullscreen support.
// ============================================================

import { useState, useCallback, useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Wrench,
  ChevronDown,
  Clock,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  User,
  Code2,
  Maximize2,
  X,
  Search,
  Globe,
  FileText,
  Terminal,
  Database,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { ToolOutputRenderer } from '../ui/ToolOutputRenderer'
import type { ToolExecution } from '../../types/process'

// ── Status config ─────────────────────────────────────────────

const statusConfig: Record<
  ToolExecution['status'],
  { icon: typeof Loader2; variant: 'info' | 'success' | 'error'; label: string }
> = {
  running: { icon: Loader2, variant: 'info', label: 'Running' },
  completed: { icon: CheckCircle2, variant: 'success', label: 'Completed' },
  failed: { icon: AlertTriangle, variant: 'error', label: 'Failed' },
}

// ── Tool icon map ─────────────────────────────────────────────

function getToolIcon(name: string) {
  const n = (name || '').toLowerCase()
  if (n.includes('search') || n.includes('web')) return <Search className="h-4 w-4" />
  if (n.includes('fetch') || n.includes('http') || n.includes('url') || n.includes('browser')) return <Globe className="h-4 w-4" />
  if (n.includes('file') || n.includes('read') || n.includes('write') || n.includes('document')) return <FileText className="h-4 w-4" />
  if (n.includes('code') || n.includes('execute') || n.includes('command') || n.includes('terminal')) return <Terminal className="h-4 w-4" />
  if (n.includes('data') || n.includes('parse') || n.includes('json')) return <Database className="h-4 w-4" />
  return <Wrench className="h-4 w-4" />
}

// ── Extract notable metadata from tool args ───────────────────

function getToolMeta(toolName: string, args: Record<string, unknown> | undefined): string | null {
  if (!args) return null
  const n = (toolName || '').toLowerCase()
  // web_search
  if (n.includes('search')) {
    const q = args.query || args.q || args.search_query
    if (q) return `Query: "${q}"`
  }
  // fetch_url / http_request / browser_navigate
  if (n.includes('fetch') || n.includes('http') || n.includes('browser') || n.includes('url')) {
    const url = args.url || args.uri || args.endpoint
    if (url) return `URL: ${url}`
  }
  // read_file / write_file
  if (n.includes('file') || n.includes('read') || n.includes('write')) {
    const path = args.path || args.file_path || args.filename
    if (path) return `File: ${path}`
  }
  // execute_code
  if (n.includes('code') || n.includes('execute')) {
    const lang = args.language || args.lang
    if (lang) return `Language: ${lang}`
  }
  // Fallback: first string value
  const firstStr = Object.values(args).find((v) => typeof v === 'string' && v.length > 0)
  if (firstStr && typeof firstStr === 'string') return firstStr.slice(0, 80)
  return null
}

// ── Full-screen detail modal ──────────────────────────────────

function DetailModal({ exec, onClose }: { exec: ToolExecution; onClose: () => void }) {
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const cfg = statusConfig[exec.status]
  const StatusIcon = cfg.icon
  const toolMeta = getToolMeta(exec.toolName, exec.toolArgs as Record<string, unknown> | undefined)

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
          'relative z-10 w-full max-w-3xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden',
          'bg-white dark:bg-[#0e0e20] border border-gray-200 dark:border-[#2a2a45]',
        )}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-[#1a1a2e] shrink-0">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gray-100 dark:bg-[#1a1a2e] text-gray-600 dark:text-gray-300">
            {getToolIcon(exec.toolName)}
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-[15px] font-bold text-gray-900 dark:text-gray-100">{exec.toolName}</h3>
            {toolMeta && <p className="text-[11px] text-gray-500 dark:text-gray-400 truncate mt-0.5">{toolMeta}</p>}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Badge variant={cfg.variant} size="sm">
              <StatusIcon className={cn('mr-1 h-3 w-3 inline', exec.status === 'running' && 'animate-spin')} />
              {cfg.label}
            </Badge>
            {exec.duration != null && (
              <span className="text-[11px] text-gray-400 font-mono tabular-nums">
                {exec.duration < 1000 ? `${exec.duration}ms` : `${(exec.duration / 1000).toFixed(1)}s`}
              </span>
            )}
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1a1a2e] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors ml-1"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Body (scrollable) */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Agent info */}
          <div className="flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
            <User className="h-3.5 w-3.5" />
            <span>Agent: <strong className="text-gray-700 dark:text-gray-200">{exec.agentName}</strong></span>
          </div>

          {/* Arguments */}
          <section>
            <div className="flex items-center gap-1.5 mb-2">
              <Code2 className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
              <span className="text-[11px] font-bold text-gray-600 dark:text-gray-300 uppercase tracking-wide">Input Arguments</span>
            </div>
            <ToolOutputRenderer
              value={exec.toolArgs}
              maxHeight={300}
              showFullscreen={false}
            />
          </section>

          {/* Result */}
          {exec.result != null && (
            <section>
              <div className="flex items-center gap-1.5 mb-2">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400" />
                <span className="text-[11px] font-bold text-emerald-700 dark:text-emerald-400 uppercase tracking-wide">Output / Result</span>
              </div>
              <ToolOutputRenderer
                value={exec.result}
                maxHeight={undefined}
                showFullscreen={false}
              />
            </section>
          )}

          {/* Error */}
          {exec.error && (
            <section>
              <div className="flex items-center gap-1.5 mb-2">
                <AlertTriangle className="h-3.5 w-3.5 text-red-500" />
                <span className="text-[11px] font-bold text-red-600 dark:text-red-400 uppercase tracking-wide">Error</span>
              </div>
              <ToolOutputRenderer
                value={exec.error}
                isError
                maxHeight={undefined}
                showFullscreen={false}
              />
            </section>
          )}
        </div>
      </motion.div>
    </div>
  )
}

// ── Single execution card ─────────────────────────────────────

function ToolExecutionCard({ exec, onExpand }: { exec: ToolExecution; onExpand: (exec: ToolExecution) => void }) {
  const [open, setOpen] = useState(false)
  const cfg = statusConfig[exec.status]
  const StatusIcon = cfg.icon
  const toolMeta = getToolMeta(exec.toolName, exec.toolArgs as Record<string, unknown> | undefined)

  // Short preview of result
  const resultPreview = exec.result
    ? (typeof exec.result === 'string' ? exec.result : JSON.stringify(exec.result)).slice(0, 120)
    : null

  return (
    <Card
      variant="default"
      padding="none"
      className={cn(
        'overflow-hidden transition-shadow',
        exec.status === 'running' && 'ring-1 ring-brand-300/40 dark:ring-brand-700/40',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Tool icon */}
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100 dark:bg-[#1e1e3a] text-gray-600 dark:text-gray-400">
          {getToolIcon(exec.toolName)}
        </span>

        {/* Tool info */}
        <div
          className="min-w-0 flex-1 cursor-pointer"
          onClick={() => setOpen((p) => !p)}
        >
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text-primary dark:text-gray-100 truncate">
              {exec.toolName}
            </span>
            <Badge variant={cfg.variant} size="sm" dot={exec.status === 'running'}>
              <StatusIcon className={cn('mr-0.5 h-3 w-3 inline', exec.status === 'running' && 'animate-spin')} aria-hidden />
              {cfg.label}
            </Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="inline-flex items-center gap-1 text-xs text-text-muted dark:text-gray-500">
              <User className="h-3 w-3" />
              {exec.agentName}
            </span>
            {exec.duration != null && (
              <span className="inline-flex items-center gap-1 text-xs text-text-muted dark:text-gray-500 tabular-nums">
                <Clock className="h-3 w-3" />
                {exec.duration < 1000 ? `${exec.duration}ms` : `${(exec.duration / 1000).toFixed(1)}s`}
              </span>
            )}
            {toolMeta && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500 truncate max-w-[180px]">{toolMeta}</span>
            )}
          </div>
          {/* Result preview (collapsed) */}
          {!open && resultPreview && (
            <p className="text-[10px] text-gray-400 dark:text-gray-500 truncate mt-0.5">
              ↳ {resultPreview}
            </p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          {/* Maximize to full modal */}
          <button
            type="button"
            onClick={() => onExpand(exec)}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1a1a2e] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            title="Xem đầy đủ"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          {/* Toggle inline expand */}
          <button
            type="button"
            onClick={() => setOpen((p) => !p)}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-[#1a1a2e] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            <ChevronDown className={cn('h-4 w-4 transition-transform duration-200', open && 'rotate-180')} />
          </button>
        </div>
      </div>

      {/* Expandable inline detail */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 pt-3 space-y-4 border-t border-border dark:border-[#2a2a45]">
              {/* Args */}
              {exec.toolArgs && Object.keys(exec.toolArgs).length > 0 ? (
                <div>
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <Code2 className="h-3 w-3 text-gray-500 dark:text-gray-400" />
                    <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase">Input Arguments</span>
                  </div>
                  <ToolOutputRenderer
                    value={exec.toolArgs}
                    maxHeight={250}
                    compact
                    showFullscreen={false}
                  />
                </div>
              ) : (
                <p className="text-[10px] text-gray-400 italic">No arguments captured</p>
              )}

              {/* Result */}
              {exec.result != null && (
                <div>
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                    <span className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 uppercase">Output</span>
                    <button
                      type="button"
                      onClick={() => onExpand(exec)}
                      className="ml-auto text-[9px] text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-200 font-medium flex items-center gap-1"
                    >
                      <Maximize2 className="h-2.5 w-2.5" />
                      Full screen
                    </button>
                  </div>
                  <ToolOutputRenderer
                    value={exec.result}
                    maxHeight={300}
                    compact
                    showFullscreen
                    onFullscreen={() => onExpand(exec)}
                  />
                </div>
              )}

              {/* Error */}
              {exec.error && (
                <div>
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <AlertTriangle className="h-3 w-3 text-red-500" />
                    <span className="text-[10px] font-semibold text-red-500 uppercase">Error</span>
                  </div>
                  <ToolOutputRenderer
                    value={exec.error}
                    maxHeight={300}
                    compact
                    isError
                    showFullscreen={false}
                  />
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  )
}

// ── Main component ────────────────────────────────────────────

export function ToolExecutionList() {
  const toolExecutions = useProcessStore((s) => s.toolExecutions)
  const [groupByAgent, setGroupByAgent] = useState(false)
  const [selectedExec, setSelectedExec] = useState<ToolExecution | null>(null)

  const toggleGroup = useCallback(() => setGroupByAgent((p) => !p), [])

  if (toolExecutions.length === 0) {
    return (
      <EmptyState
        icon={<Wrench className="h-10 w-10" />}
        title="No tool executions"
        description="Tool calls will appear here as agents execute them."
      />
    )
  }

  const grouped = groupByAgent
    ? toolExecutions.reduce<Record<string, ToolExecution[]>>((acc, exec) => {
        const key = exec.agentName || exec.agentId
        if (!acc[key]) acc[key] = []
        acc[key].push(exec)
        return acc
      }, {})
    : null

  return (
    <>
      {/* Full-screen modal */}
      <AnimatePresence>
        {selectedExec && (
          <DetailModal exec={selectedExec} onClose={() => setSelectedExec(null)} />
        )}
      </AnimatePresence>

      <div className="space-y-3 animate-fade-in">
        {/* Controls */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted dark:text-gray-500">
            {toolExecutions.length} execution{toolExecutions.length !== 1 ? 's' : ''}
          </span>
          <button
            type="button"
            onClick={toggleGroup}
            className={cn(
              'text-xs font-medium px-2.5 py-1 rounded-full transition-colors',
              groupByAgent
                ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-400'
                : 'bg-gray-100 text-text-secondary dark:bg-[#1e1e3a] dark:text-gray-400',
            )}
          >
            {groupByAgent ? 'Grouped by agent' : 'Chronological'}
          </button>
        </div>

        {/* Grouped view */}
        {grouped ? (
          Object.entries(grouped).map(([agentName, execs]) => (
            <div key={agentName} className="space-y-2">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold text-text-secondary dark:text-gray-300">
                <User className="h-3.5 w-3.5" />
                {agentName}
                <span className="text-text-muted dark:text-gray-500 font-normal">({execs.length})</span>
              </h4>
              <div className="space-y-2 pl-1">
                {execs.map((exec) => (
                  <ToolExecutionCard key={exec.id} exec={exec} onExpand={setSelectedExec} />
                ))}
              </div>
            </div>
          ))
        ) : (
          <div className="space-y-2">
            {toolExecutions.map((exec) => (
              <ToolExecutionCard key={exec.id} exec={exec} onExpand={setSelectedExec} />
            ))}
          </div>
        )}
      </div>
    </>
  )
}
