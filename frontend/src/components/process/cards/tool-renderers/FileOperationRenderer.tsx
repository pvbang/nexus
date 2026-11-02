// ============================================================
// Nexus - FileOperationRenderer
// Specialized renderer for write_file, read_file, list_directory
// Shows file icon + path + content with syntax highlighting
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import {
  FileText, FolderOpen, FilePlus, FileX, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, Copy, Check,
} from 'lucide-react'
import { cn } from '../../../../lib/cn'
import { getAgentColor } from '../../../../lib/constants'
import { Badge } from '../../../ui/Badge'
import { Spinner } from '../../../ui/Spinner'
import { ToolOutputRenderer } from '../../../ui/ToolOutputRenderer'
import type { ToolExecution } from '../../../../types/process'

interface FileOperationRendererProps {
  execution: ToolExecution
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Determine file operation type */
function getFileAction(toolName: string): { icon: typeof FileText; label: string; color: string } {
  const name = (toolName || '').toLowerCase()
  if (name.includes('write') || name.includes('create') || name.includes('save')) {
    return { icon: FilePlus, label: 'Write', color: 'text-emerald-500' }
  }
  if (name.includes('delete') || name.includes('remove')) {
    return { icon: FileX, label: 'Delete', color: 'text-red-500' }
  }
  if (name.includes('list') || name.includes('directory') || name.includes('dir')) {
    return { icon: FolderOpen, label: 'List', color: 'text-amber-500' }
  }
  return { icon: FileText, label: 'Read', color: 'text-blue-500' }
}

/** Guess language from file extension */
function getLanguageFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  const map: Record<string, string> = {
    py: 'python', js: 'javascript', ts: 'typescript', tsx: 'tsx', jsx: 'jsx',
    json: 'json', yaml: 'yaml', yml: 'yaml', md: 'markdown',
    html: 'html', css: 'css', sql: 'sql', sh: 'bash', bash: 'bash',
    rs: 'rust', go: 'go', rb: 'ruby', java: 'java', cpp: 'cpp', c: 'c',
    toml: 'toml', xml: 'xml', csv: 'csv', txt: 'text',
  }
  return map[ext] || 'text'
}

export function FileOperationRenderer({ execution }: FileOperationRendererProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'
  const hasOutput = !!(execution.result || execution.error)

  const action = getFileAction(execution.toolName)
  const ActionIcon = action.icon

  const filePath = (execution.toolArgs?.path as string)
    || (execution.toolArgs?.file_path as string)
    || (execution.toolArgs?.filename as string)
    || (execution.toolArgs?.directory as string)
    || ''
  const content = (execution.toolArgs?.content as string) || ''
  const language = filePath ? getLanguageFromPath(filePath) : 'text'

  const handleCopyPath = () => {
    navigator.clipboard.writeText(filePath)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border bg-white px-3.5 py-2.5 text-sm',
        'dark:bg-[#1a1a2e]',
        isFailed
          ? 'border-red-200 dark:border-red-800'
          : 'border-sky-200 dark:border-sky-800/50',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <ActionIcon className={cn('h-4 w-4', action.color)} />
        <span className="font-medium text-text-primary dark:text-gray-100 text-xs">
          {execution.toolName}
        </span>
        <Badge variant="info" size="sm">{action.label}</Badge>
        <span className="text-xs text-text-muted dark:text-gray-500">
          by{' '}
          <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>
            {execution.agentName}
          </span>
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {execution.duration != null && (
            <Badge variant="default" size="sm">{formatDuration(execution.duration)}</Badge>
          )}
          {isRunning && <Spinner size="xs" className="text-sky-500" />}
          {!isRunning && !isFailed && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isFailed && <XCircle className="h-4 w-4 text-red-500" />}
        </span>
      </div>

      {/* File path */}
      {filePath && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <div
            className={cn(
              'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono max-w-full',
              'bg-sky-50 border border-sky-100 text-sky-800',
              'dark:bg-sky-900/20 dark:border-sky-800/50 dark:text-sky-300',
            )}
          >
            <FileText className="h-3 w-3 shrink-0" />
            <span className="truncate">{filePath}</span>
            {language !== 'text' && (
              <Badge variant="default" size="sm">{language}</Badge>
            )}
          </div>
          <button
            type="button"
            onClick={handleCopyPath}
            className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 transition-colors"
            title="Copy path"
          >
            {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
          </button>
        </div>
      )}

      {/* Written content preview (for write operations) */}
      {content && action.label === 'Write' && (
        <div className="mt-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[9px] text-gray-400 uppercase font-bold">Written Content</span>
            <span className="text-[9px] text-gray-400 font-mono">{content.length.toLocaleString()} chars</span>
          </div>
          <pre
            className={cn(
              'text-[11px] leading-relaxed font-mono p-2.5 rounded-lg overflow-auto whitespace-pre-wrap break-words',
              'bg-gray-50 text-gray-800 border border-gray-100',
              'dark:bg-[#0d0d1a] dark:text-gray-300 dark:border-[#1a1a2e]',
            )}
            style={{ maxHeight: expanded ? 400 : 100 }}
          >
            {content}
          </pre>
          {content.length > 200 && (
            <button
              type="button"
              onClick={() => setExpanded(p => !p)}
              className="flex items-center gap-1 mt-1 text-xs text-sky-600 dark:text-sky-400 hover:underline"
            >
              {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              {expanded ? 'Collapse' : 'Show full content'}
            </button>
          )}
        </div>
      )}

      {/* Result */}
      {hasOutput && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setExpanded(p => !p)}
            className="flex items-center gap-1 text-xs text-sky-600 dark:text-sky-400 hover:underline"
          >
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {expanded ? 'Hide result' : (isFailed ? 'Show error' : 'Show result')}
          </button>
          {expanded && (
            <div className="mt-1.5">
              <ToolOutputRenderer
                value={execution.error || execution.result}
                maxHeight={400}
                compact
                isError={isFailed}
                showFullscreen
              />
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}
