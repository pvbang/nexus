// ============================================================
// Nexus - SearchResultsRenderer
// Specialized renderer for web_search, search_* tools
// Shows search results as card list with title + snippet + URL
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Search, ExternalLink, ChevronDown, ChevronUp, CheckCircle2, XCircle, Globe } from 'lucide-react'
import { cn } from '../../../../lib/cn'
import { getAgentColor } from '../../../../lib/constants'
import { Badge } from '../../../ui/Badge'
import { Spinner } from '../../../ui/Spinner'
import { ToolOutputRenderer } from '../../../ui/ToolOutputRenderer'
import type { ToolExecution } from '../../../../types/process'

interface SearchResultsRendererProps {
  execution: ToolExecution
}

interface SearchResult {
  title: string
  url?: string
  snippet?: string
  description?: string
  source?: string
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Try to parse search results from the output */
function parseSearchResults(result: string | undefined): SearchResult[] {
  if (!result) return []
  try {
    const parsed = typeof result === 'string' ? JSON.parse(result) : result
    // Handle array of results
    if (Array.isArray(parsed)) {
      return parsed.map((item: any) => ({
        title: item.title || item.name || '',
        url: item.url || item.link || item.href || '',
        snippet: item.snippet || item.description || item.content?.slice(0, 200) || '',
        source: item.source || '',
      })).filter((r: SearchResult) => r.title || r.url)
    }
    // Handle { results: [...] }
    if (parsed.results && Array.isArray(parsed.results)) {
      return parseSearchResults(JSON.stringify(parsed.results))
    }
    // Handle { organic_results: [...] }
    if (parsed.organic_results && Array.isArray(parsed.organic_results)) {
      return parseSearchResults(JSON.stringify(parsed.organic_results))
    }
  } catch {
    // Not parseable — return empty
  }
  return []
}

export function SearchResultsRenderer({ execution }: SearchResultsRendererProps) {
  const [expanded, setExpanded] = useState(true)
  const [showRaw, setShowRaw] = useState(false)

  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'

  const query = (execution.toolArgs?.query as string)
    || (execution.toolArgs?.q as string)
    || (execution.toolArgs?.search_query as string)
    || ''

  const results = parseSearchResults(execution.result)
  const hasResults = results.length > 0

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
          : 'border-amber-200 dark:border-amber-800/50',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <Search className="h-4 w-4 text-amber-500 dark:text-amber-400" />
        <span className="font-medium text-text-primary dark:text-gray-100 text-xs">
          {execution.toolName}
        </span>
        <span className="text-xs text-text-muted dark:text-gray-500">
          by{' '}
          <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>
            {execution.agentName}
          </span>
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {hasResults && (
            <Badge variant="info" size="sm">{results.length} results</Badge>
          )}
          {execution.duration != null && (
            <Badge variant="default" size="sm">{formatDuration(execution.duration)}</Badge>
          )}
          {isRunning && <Spinner size="xs" className="text-amber-500" />}
          {!isRunning && !isFailed && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isFailed && <XCircle className="h-4 w-4 text-red-500" />}
        </span>
      </div>

      {/* Search query */}
      {query && (
        <div className="mt-1.5 flex items-center gap-2">
          <div className={cn(
            'flex-1 flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs',
            'bg-amber-50 border border-amber-100 text-amber-800',
            'dark:bg-amber-900/20 dark:border-amber-800/50 dark:text-amber-200',
          )}>
            <Search className="h-3 w-3 text-amber-500 shrink-0" />
            <span className="font-medium">{query}</span>
          </div>
        </div>
      )}

      {/* Results list */}
      {hasResults && expanded && (
        <div className="mt-2 space-y-1.5">
          {results.slice(0, 8).map((result, idx) => (
            <div
              key={idx}
              className={cn(
                'flex items-start gap-2 px-2.5 py-2 rounded-lg border',
                'bg-gray-50 border-gray-100',
                'dark:bg-[#12122a] dark:border-[#2a2a45]',
                'hover:border-amber-200 dark:hover:border-amber-800/50 transition-colors',
              )}
            >
              <Globe className="h-3.5 w-3.5 text-gray-400 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                {result.url ? (
                  <a
                    href={result.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
                  >
                    {result.title || result.url}
                    <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                  </a>
                ) : (
                  <span className="text-xs font-medium text-text-primary dark:text-gray-200">
                    {result.title}
                  </span>
                )}
                {result.url && result.title && (
                  <div className="text-[10px] text-gray-400 truncate mt-0.5">{result.url}</div>
                )}
                {result.snippet && (
                  <p className="text-[11px] text-text-secondary dark:text-gray-400 mt-0.5 line-clamp-2">
                    {result.snippet}
                  </p>
                )}
              </div>
            </div>
          ))}
          {results.length > 8 && (
            <span className="text-[10px] text-gray-400 pl-2">+{results.length - 8} more results</span>
          )}
        </div>
      )}

      {/* Toggle / raw view */}
      {(hasResults || execution.result) && (
        <div className="mt-2 flex items-center gap-2">
          {hasResults && (
            <button
              type="button"
              onClick={() => setExpanded(p => !p)}
              className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400 hover:underline"
            >
              {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              {expanded ? 'Collapse' : 'Show results'}
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowRaw(p => !p)}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            {showRaw ? 'Hide raw' : 'Show raw'}
          </button>
        </div>
      )}

      {showRaw && execution.result && (
        <div className="mt-2">
          <ToolOutputRenderer
            value={execution.result}
            maxHeight={300}
            compact
            showFullscreen
          />
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
