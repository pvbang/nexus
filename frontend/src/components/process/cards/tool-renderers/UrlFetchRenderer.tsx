// ============================================================
// Nexus - UrlFetchRenderer
// Specialized renderer for fetch_url, browse_webpage, http_request
// Shows URL badge + response preview + content excerpt
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Globe, ExternalLink, ChevronDown, ChevronUp, CheckCircle2, XCircle } from 'lucide-react'
import { cn } from '../../../../lib/cn'
import { getAgentColor } from '../../../../lib/constants'
import { Badge } from '../../../ui/Badge'
import { Spinner } from '../../../ui/Spinner'
import { ToolOutputRenderer } from '../../../ui/ToolOutputRenderer'
import type { ToolExecution } from '../../../../types/process'

interface UrlFetchRendererProps {
  execution: ToolExecution
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function UrlFetchRenderer({ execution }: UrlFetchRendererProps) {
  const [expanded, setExpanded] = useState(false)

  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'
  const hasOutput = !!(execution.result || execution.error)

  const url = (execution.toolArgs?.url as string)
    || (execution.toolArgs?.target_url as string)
    || (execution.toolArgs?.webpage_url as string)
    || ''
  const method = ((execution.toolArgs?.method as string) || 'GET').toUpperCase()

  // Measure content length for size badge
  let contentLength = 0
  const outputStr = execution.result || ''
  if (typeof outputStr === 'string') {
    contentLength = outputStr.length
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
          : 'border-purple-200 dark:border-purple-800/50',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <Globe className="h-4 w-4 text-purple-500 dark:text-purple-400" />
        <span className="font-medium text-text-primary dark:text-gray-100 text-xs">
          {execution.toolName}
        </span>
        <Badge variant="purple" size="sm">{method}</Badge>
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
          {isRunning && <Spinner size="xs" className="text-purple-500" />}
          {!isRunning && !isFailed && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isFailed && <XCircle className="h-4 w-4 text-red-500" />}
        </span>
      </div>

      {/* URL badge */}
      {url && (
        <div className="mt-1.5">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs max-w-full',
              'bg-purple-50 border border-purple-100 text-purple-700',
              'dark:bg-purple-900/20 dark:border-purple-800/50 dark:text-purple-300',
              'hover:bg-purple-100 dark:hover:bg-purple-900/30 transition-colors',
            )}
          >
            <Globe className="h-3 w-3 shrink-0" />
            <span className="truncate">{url}</span>
            <ExternalLink className="h-2.5 w-2.5 shrink-0 opacity-50" />
          </a>
        </div>
      )}

      {/* Expandable output (single render — no duplicate preview) */}
      {hasOutput && !isFailed && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setExpanded(p => !p)}
            className="flex items-center gap-1 text-xs text-purple-600 dark:text-purple-400 hover:underline"
          >
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {expanded
              ? 'Collapse'
              : `Show response${contentLength > 300 ? ` (${(contentLength / 1024).toFixed(1)}KB)` : ''}`}
          </button>

          {expanded && (
            <div className="mt-1.5">
              <ToolOutputRenderer
                value={execution.error || execution.result}
                maxHeight={500}
                compact
                isError={isFailed}
                showFullscreen
              />
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {isFailed && execution.error && (
        <div className="mt-2 text-[11px] text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded px-2 py-1">
          {execution.error}
        </div>
      )}
    </motion.div>
  )
}
