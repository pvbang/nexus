// ============================================================
// Nexus - TaskDocRenderer
// Specialized renderer for task_doc_write, task_doc_read,
// task_doc_edit, task_doc_search, task_doc_list, task_doc_delete
//
// Shows document operations with beautiful markdown-aware display,
// document icon, mode badge, and content preview with syntax
// highlighting for markdown content.
// ============================================================

import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  FileText, FilePlus, FileSearch, FileEdit, FileX, FolderOpen,
  ChevronDown, ChevronUp, CheckCircle2, XCircle, Copy, Check,
  BookOpen, Pencil, PlusCircle, Replace, ScrollText, Layers,
} from 'lucide-react'
import { cn } from '../../../../lib/cn'
import { getAgentColor } from '../../../../lib/constants'
import { Badge } from '../../../ui/Badge'
import { Spinner } from '../../../ui/Spinner'
import { MarkdownRenderer } from '../../../ui/MarkdownRenderer'
import { ToolOutputRenderer } from '../../../ui/ToolOutputRenderer'
import type { ToolExecution } from '../../../../types/process'

interface TaskDocRendererProps {
  execution: ToolExecution
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// ── Doc operation detection ───────────────────────────────────

interface DocOperation {
  icon: typeof FileText
  label: string
  color: string
  bgColor: string
  borderColor: string
}

function getDocOperation(toolName: string): DocOperation {
  const name = (toolName || '').toLowerCase()
  if (name.includes('write')) {
    return {
      icon: FilePlus,
      label: 'Write',
      color: 'text-emerald-600 dark:text-emerald-400',
      bgColor: 'bg-emerald-50 dark:bg-emerald-900/20',
      borderColor: 'border-emerald-200 dark:border-emerald-800/50',
    }
  }
  if (name.includes('edit')) {
    return {
      icon: FileEdit,
      label: 'Edit',
      color: 'text-amber-600 dark:text-amber-400',
      bgColor: 'bg-amber-50 dark:bg-amber-900/20',
      borderColor: 'border-amber-200 dark:border-amber-800/50',
    }
  }
  if (name.includes('search')) {
    return {
      icon: FileSearch,
      label: 'Search',
      color: 'text-blue-600 dark:text-blue-400',
      bgColor: 'bg-blue-50 dark:bg-blue-900/20',
      borderColor: 'border-blue-200 dark:border-blue-800/50',
    }
  }
  if (name.includes('delete')) {
    return {
      icon: FileX,
      label: 'Delete',
      color: 'text-red-600 dark:text-red-400',
      bgColor: 'bg-red-50 dark:bg-red-900/20',
      borderColor: 'border-red-200 dark:border-red-800/50',
    }
  }
  if (name.includes('list')) {
    return {
      icon: FolderOpen,
      label: 'List',
      color: 'text-purple-600 dark:text-purple-400',
      bgColor: 'bg-purple-50 dark:bg-purple-900/20',
      borderColor: 'border-purple-200 dark:border-purple-800/50',
    }
  }
  // Default: read
  return {
    icon: BookOpen,
    label: 'Read',
    color: 'text-sky-600 dark:text-sky-400',
    bgColor: 'bg-sky-50 dark:bg-sky-900/20',
    borderColor: 'border-sky-200 dark:border-sky-800/50',
  }
}

// ── Mode icon & badge ─────────────────────────────────────────

function getModeInfo(mode?: string): { icon: typeof PlusCircle; label: string; color: string } {
  switch (mode) {
    case 'append':
      return { icon: PlusCircle, label: 'Append', color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
    case 'prepend':
      return { icon: Layers, label: 'Prepend', color: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' }
    case 'replace':
      return { icon: Replace, label: 'Replace', color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' }
    default:
      return { icon: Pencil, label: mode || 'Write', color: 'bg-gray-100 text-gray-700 dark:bg-gray-800/50 dark:text-gray-300' }
  }
}

// ── Well-known doc name icons ─────────────────────────────────

const DOC_ICONS: Record<string, string> = {
  TASK_BRIEF: '📋',
  PROGRESS: '📈',
  FINDINGS: '🔍',
  DECISIONS: '⚖️',
  SCRATCHPAD: '📝',
}

function getDocEmoji(docName: string): string {
  return DOC_ICONS[docName.toUpperCase()] || '📄'
}

// ── Content preview with markdown awareness ───────────────────

function ContentPreview({
  content,
  maxHeight,
  expanded,
}: {
  content: string
  maxHeight: number
  expanded: boolean
}) {
  // Detect if content looks like markdown
  const isMarkdown = /^#+\s|^\-\s|\*\*|```|^\d+\.\s/m.test(content)

  if (isMarkdown && expanded) {
    return (
      <div
        className={cn(
          'rounded-lg overflow-auto border',
          'bg-white dark:bg-[#0d0d1a]',
          'border-gray-100 dark:border-[#1a1a2e]',
          'p-3 prose-nexus text-[12px] leading-relaxed',
        )}
        style={{ maxHeight: expanded ? maxHeight : 80 }}
      >
        <MarkdownRenderer content={content} />
      </div>
    )
  }

  return (
    <pre
      className={cn(
        'text-[11px] leading-relaxed font-mono p-3 rounded-lg overflow-auto whitespace-pre-wrap break-words',
        'bg-gray-50 text-gray-800 border border-gray-100',
        'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
      )}
      style={{ maxHeight: expanded ? maxHeight : 80 }}
    >
      {content}
    </pre>
  )
}

// ── Main component ────────────────────────────────────────────

export function TaskDocRenderer({ execution }: TaskDocRendererProps) {
  const [expanded, setExpanded] = useState(false)
  const [showResult, setShowResult] = useState(false)
  const [copied, setCopied] = useState(false)

  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'
  const hasOutput = !!(execution.result || execution.error)

  const operation = getDocOperation(execution.toolName)
  const OperationIcon = operation.icon

  // Extract args
  const docName = (execution.toolArgs?.doc as string) || 'SCRATCHPAD'
  const content = (execution.toolArgs?.content as string) || ''
  const mode = (execution.toolArgs?.mode as string) || undefined
  const section = (execution.toolArgs?.section as string) || undefined
  const query = (execution.toolArgs?.query as string) || undefined
  const search = (execution.toolArgs?.search as string) || undefined
  const replace = (execution.toolArgs?.replace as string) || undefined

  const modeInfo = getModeInfo(mode)
  const ModeIcon = modeInfo.icon
  const docEmoji = getDocEmoji(docName)

  // Determine what content to show based on operation type
  const displayContent = useMemo(() => {
    if (content) return content
    if (search && replace) return `Find: ${search}\nReplace: ${replace}`
    if (query) return `Search: "${query}"`
    return ''
  }, [content, search, replace, query])

  const handleCopyContent = () => {
    navigator.clipboard.writeText(displayContent || execution.result || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-xl border overflow-hidden text-sm',
        'bg-white dark:bg-[#1a1a2e]',
        isFailed
          ? 'border-red-200 dark:border-red-800/60'
          : operation.borderColor,
      )}
    >
      {/* ── Header ──────────────────────────────────────────────── */}
      <div
        className={cn(
          'flex items-center gap-2.5 px-3.5 py-2.5',
          operation.bgColor,
        )}
      >
        {/* Operation icon */}
        <div className={cn(
          'flex items-center justify-center w-7 h-7 rounded-lg',
          'bg-white/80 dark:bg-[#0d0d1a]/60 shadow-sm',
        )}>
          <OperationIcon className={cn('h-4 w-4', operation.color)} />
        </div>

        {/* Tool name + doc name */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold text-gray-800 dark:text-gray-100">
              {execution.toolName}
            </span>
            <Badge variant="default" size="sm">{operation.label}</Badge>
            {mode && (
              <span className={cn(
                'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-medium',
                modeInfo.color,
              )}>
                <ModeIcon className="h-2.5 w-2.5" />
                {modeInfo.label}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            <span className="text-[10px] text-gray-500 dark:text-gray-400">
              by{' '}
              <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>
                {execution.agentName}
              </span>
            </span>
          </div>
        </div>

        {/* Status */}
        <span className="flex items-center gap-1.5 shrink-0">
          {execution.duration != null && (
            <Badge variant="default" size="sm">
              {formatDuration(execution.duration)}
            </Badge>
          )}
          {isRunning && <Spinner size="xs" className={operation.color} />}
          {!isRunning && !isFailed && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isFailed && <XCircle className="h-4 w-4 text-red-500" />}
        </span>
      </div>

      {/* ── Document info bar ───────────────────────────────────── */}
      <div className="px-3.5 py-2 border-t border-gray-100 dark:border-[#22223a]">
        <div className="flex items-center gap-2">
          {/* Doc badge */}
          <div
            className={cn(
              'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono',
              'bg-gray-50 border border-gray-150 text-gray-700',
              'dark:bg-[#12122a] dark:border-[#2a2a45] dark:text-gray-300',
            )}
          >
            <span className="text-sm">{docEmoji}</span>
            <span className="font-semibold">{docName}</span>
            <span className="text-gray-400 dark:text-gray-500">.md</span>
          </div>

          {/* Section (if specified) */}
          {section && (
            <div className="flex items-center gap-1 text-[10px] text-gray-500 dark:text-gray-400">
              <ScrollText className="h-3 w-3" />
              <span>§ {section}</span>
            </div>
          )}

          {/* Copy button */}
          {displayContent && (
            <button
              type="button"
              onClick={handleCopyContent}
              className="ml-auto p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 transition-colors"
              title="Copy content"
            >
              {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
            </button>
          )}
        </div>
      </div>

      {/* ── Content preview ─────────────────────────────────────── */}
      {displayContent && (
        <div className="px-3.5 pb-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[9px] text-gray-400 dark:text-gray-500 uppercase font-bold">
              {operation.label === 'Write' ? 'Content Written' :
               operation.label === 'Edit' ? 'Edit Operation' :
               operation.label === 'Search' ? 'Search Query' :
               'Content'}
            </span>
            <span className="text-[9px] text-gray-400 dark:text-gray-500 font-mono">
              {displayContent.length.toLocaleString()} chars
            </span>
          </div>

          <ContentPreview
            content={displayContent}
            maxHeight={expanded ? 500 : 80}
            expanded={expanded}
          />

          {displayContent.length > 150 && (
            <button
              type="button"
              onClick={() => setExpanded(p => !p)}
              className={cn(
                'flex items-center gap-1 mt-1.5 text-xs hover:underline',
                operation.color,
              )}
            >
              {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              {expanded ? 'Collapse' : 'Show full content'}
            </button>
          )}
        </div>
      )}

      {/* ── Result / Error ──────────────────────────────────────── */}
      {hasOutput && (
        <div className="px-3.5 pb-3 border-t border-gray-100 dark:border-[#22223a] pt-2">
          <button
            type="button"
            onClick={() => setShowResult(p => !p)}
            className={cn(
              'flex items-center gap-1 text-xs hover:underline',
              isFailed ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400',
            )}
          >
            {showResult ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {showResult ? 'Hide' : (isFailed ? 'Show error' : 'Show result')}
          </button>

          <AnimatePresence initial={false}>
            {showResult && (
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
                    maxHeight={400}
                    compact
                    isError={isFailed}
                    showFullscreen
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </motion.div>
  )
}
