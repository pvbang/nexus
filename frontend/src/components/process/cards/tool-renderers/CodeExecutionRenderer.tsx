// ============================================================
// Nexus - CodeExecutionRenderer
// Specialized renderer for execute_code / execute_command tools
// Shows code input with syntax highlighting + terminal output
// ============================================================

import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Terminal, ChevronDown, ChevronUp, CheckCircle2, XCircle, Copy, Check, Maximize2 } from 'lucide-react'
import { cn } from '../../../../lib/cn'
import { getAgentColor } from '../../../../lib/constants'
import { Badge } from '../../../ui/Badge'
import { Spinner } from '../../../ui/Spinner'
// ToolOutputRenderer available for future enhanced rendering
import type { ToolExecution } from '../../../../types/process'

interface CodeExecutionRendererProps {
  execution: ToolExecution
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      className="p-1 rounded hover:bg-gray-700 text-gray-500 hover:text-gray-300 transition-colors"
      title="Copy"
    >
      {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
    </button>
  )
}

export function CodeExecutionRenderer({ execution }: CodeExecutionRendererProps) {
  const [expanded, setExpanded] = useState(true)
  const [showFullOutput, setShowFullOutput] = useState(false)

  const isRunning = execution.status === 'running'
  const isFailed = execution.status === 'failed'
  const hasOutput = !!(execution.result || execution.error)

  // Extract language and code from toolArgs
  const language = (execution.toolArgs?.language as string) || 'bash'
  const code = (execution.toolArgs?.code as string) || (execution.toolArgs?.command as string) || ''
  const outputStr = execution.error || execution.result || ''

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border overflow-hidden',
        isFailed
          ? 'border-red-300 dark:border-red-800'
          : 'border-emerald-200 dark:border-emerald-800/50',
      )}
    >
      {/* Header */}
      <div
        className={cn(
          'flex items-center gap-2 px-3.5 py-2',
          isFailed
            ? 'bg-red-50 dark:bg-red-900/20'
            : 'bg-emerald-50 dark:bg-emerald-900/20',
        )}
      >
        <Terminal className={cn('h-4 w-4', isFailed ? 'text-red-500' : 'text-emerald-500')} />
        <span className="font-mono font-medium text-xs text-text-primary dark:text-gray-100">
          {execution.toolName}
        </span>
        <Badge variant="default" size="sm">{language}</Badge>
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
          {isRunning && <Spinner size="xs" className="text-emerald-500" />}
          {!isRunning && !isFailed && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isFailed && <XCircle className="h-4 w-4 text-red-500" />}
        </span>
      </div>

      {/* Code Input */}
      {code && (
        <div className="relative">
          <div className="flex items-center justify-between px-3 py-1 bg-gray-800 dark:bg-[#0a0a1a]">
            <span className="text-[9px] text-gray-500 font-mono uppercase">Input ({language})</span>
            <CopyBtn text={code} />
          </div>
          <pre
            className={cn(
              'text-[11px] leading-relaxed font-mono px-3 py-2 overflow-auto whitespace-pre-wrap break-words',
              'bg-gray-900 text-gray-200 dark:bg-[#0d0d1a] dark:text-gray-300',
            )}
            style={{ maxHeight: expanded ? 300 : 80 }}
          >
            {code}
          </pre>
        </div>
      )}

      {/* Output */}
      {hasOutput && (
        <div className="relative">
          <div
            className={cn(
              'flex items-center justify-between px-3 py-1',
              isFailed
                ? 'bg-red-100 dark:bg-red-900/30'
                : 'bg-gray-100 dark:bg-[#12122a]',
            )}
          >
            <span className={cn(
              'text-[9px] font-mono uppercase font-bold',
              isFailed ? 'text-red-600 dark:text-red-400' : 'text-gray-500',
            )}>
              {isFailed ? '✗ Error' : '✓ Output'}
            </span>
            <div className="flex items-center gap-1">
              {typeof outputStr === 'string' && outputStr.length > 200 && (
                <button
                  type="button"
                  onClick={() => setShowFullOutput(true)}
                  className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-gray-400 transition-colors"
                  title="Fullscreen"
                >
                  <Maximize2 className="h-3 w-3" />
                </button>
              )}
              <button
                type="button"
                onClick={() => setExpanded(p => !p)}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-gray-400 transition-colors"
              >
                {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              </button>
            </div>
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
                <pre
                  className={cn(
                    'text-[11px] leading-relaxed font-mono px-3 py-2 overflow-auto whitespace-pre-wrap break-words',
                    isFailed
                      ? 'bg-red-50 text-red-700 dark:bg-red-900/10 dark:text-red-300'
                      : 'bg-gray-50 text-gray-800 dark:bg-[#0d0d1a] dark:text-gray-300',
                  )}
                  style={{ maxHeight: 400 }}
                >
                  {typeof outputStr === 'string' ? outputStr : JSON.stringify(outputStr, null, 2)}
                </pre>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Fullscreen output modal */}
      {showFullOutput && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => setShowFullOutput(false)} />
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="relative z-10 w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden bg-white dark:bg-[#0e0e20] border border-gray-200 dark:border-[#2a2a45]"
          >
            <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-100 dark:border-[#1a1a2e] bg-gray-800 dark:bg-[#0a0a1a]">
              <Terminal className="h-4 w-4 text-emerald-500" />
              <span className="text-sm font-mono font-bold text-gray-200">{execution.toolName}</span>
              <span className="text-[10px] text-gray-500">by{' '}
                <span className={cn('font-semibold', getAgentColor(undefined, execution.agentName).text)}>
                  {execution.agentName}
                </span>
              </span>
              <button
                type="button"
                onClick={() => setShowFullOutput(false)}
                className="ml-auto text-gray-400 hover:text-white transition-colors text-xs"
              >
                ✕ Close
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {code && (
                <div>
                  <div className="px-4 py-1 bg-gray-700 text-[9px] text-gray-400 font-mono uppercase">Input ({language})</div>
                  <pre className="text-[11px] font-mono px-4 py-3 bg-gray-900 text-gray-200 whitespace-pre-wrap break-words">{code}</pre>
                </div>
              )}
              <div>
                <div className={cn('px-4 py-1 text-[9px] font-mono uppercase font-bold', isFailed ? 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400' : 'bg-gray-100 text-gray-500 dark:bg-[#12122a]')}>
                  {isFailed ? 'Error' : 'Output'}
                </div>
                <pre className={cn('text-[11px] font-mono px-4 py-3 whitespace-pre-wrap break-words', isFailed ? 'bg-red-50 text-red-700 dark:bg-red-900/10 dark:text-red-300' : 'bg-gray-50 text-gray-800 dark:bg-[#0d0d1a] dark:text-gray-300')}>
                  {typeof outputStr === 'string' ? outputStr : JSON.stringify(outputStr, null, 2)}
                </pre>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </motion.div>
  )
}
