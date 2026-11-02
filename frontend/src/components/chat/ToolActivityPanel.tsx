// ============================================================
// Nexus - Tool Activity Panel (Hermes-style)
// Shows real-time tool calls with status, timing, output
// ============================================================

import { useState, useEffect, useRef } from 'react'
import {
  Wrench, ChevronDown, ChevronRight, CheckCircle2,
  XCircle, Loader2, Code2, Search, Globe, FileText,
  Database, Terminal, Zap, Braces, Copy, Check,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../../lib/cn'

// ── Types ─────────────────────────────────────────────────────

export interface ToolCall {
  id: string
  name: string
  params?: Record<string, unknown> | string
  output?: string
  status: 'running' | 'done' | 'failed'
  startTime: number
  endTime?: number
  agentName?: string
}

export interface ToolActivityPanelProps {
  toolCalls: ToolCall[]
  className?: string
  compact?: boolean
}

// ── Icon mapping ──────────────────────────────────────────────

function getToolIcon(name: string) {
  const lower = name.toLowerCase()
  if (lower.includes('search') || lower.includes('web_search') || lower.includes('google'))
    return <Search className="w-3.5 h-3.5" />
  if (lower.includes('code') || lower.includes('python') || lower.includes('execute'))
    return <Code2 className="w-3.5 h-3.5" />
  if (lower.includes('url') || lower.includes('fetch') || lower.includes('http') || lower.includes('browse'))
    return <Globe className="w-3.5 h-3.5" />
  if (lower.includes('file') || lower.includes('read') || lower.includes('write'))
    return <FileText className="w-3.5 h-3.5" />
  if (lower.includes('db') || lower.includes('sql') || lower.includes('query'))
    return <Database className="w-3.5 h-3.5" />
  if (lower.includes('shell') || lower.includes('bash') || lower.includes('cmd'))
    return <Terminal className="w-3.5 h-3.5" />
  if (lower.includes('api') || lower.includes('request'))
    return <Braces className="w-3.5 h-3.5" />
  return <Wrench className="w-3.5 h-3.5" />
}

// ── Elapsed Timer ─────────────────────────────────────────────

function ElapsedTime({ startTime, endTime }: { startTime: number; endTime?: number }) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (endTime) {
      setElapsed(endTime - startTime)
      return
    }

    const interval = setInterval(() => {
      setElapsed(Date.now() - startTime)
    }, 100)

    return () => clearInterval(interval)
  }, [startTime, endTime])

  const seconds = (elapsed / 1000).toFixed(1)
  return (
    <span className="text-[10px] font-mono text-gray-400 dark:text-gray-500 tabular-nums">
      {seconds}s
    </span>
  )
}

// ── Single Tool Call Item ─────────────────────────────────────

function ToolCallItem({ call, compact }: { call: ToolCall; compact?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const statusColors = {
    running: 'text-blue-500 bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800',
    done: 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800',
    failed: 'text-red-500 bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800',
  }

  const statusIcons = {
    running: <Loader2 className="w-3 h-3 animate-spin" />,
    done: <CheckCircle2 className="w-3 h-3" />,
    failed: <XCircle className="w-3 h-3" />,
  }

  const paramsPreview = call.params
    ? typeof call.params === 'string'
      ? call.params.slice(0, 80)
      : JSON.stringify(call.params).slice(0, 80)
    : ''

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={cn(
        'border rounded-lg overflow-hidden transition-colors',
        statusColors[call.status]
      )}
    >
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-black/[0.02] dark:hover:bg-white/[0.02] transition-colors"
      >
        {/* Tool icon */}
        <div className="shrink-0 opacity-70">
          {getToolIcon(call.name)}
        </div>

        {/* Tool name + agent */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[12px] font-semibold truncate">
              {call.name}
            </span>
            {call.agentName && (
              <span className="text-[10px] opacity-60 truncate">
                ({call.agentName})
              </span>
            )}
          </div>
          {!compact && paramsPreview && (
            <p className="text-[10px] opacity-50 truncate mt-0.5 font-mono">
              {paramsPreview}
            </p>
          )}
        </div>

        {/* Status + Time */}
        <div className="flex items-center gap-2 shrink-0">
          <ElapsedTime startTime={call.startTime} endTime={call.endTime} />
          {statusIcons[call.status]}
          {(call.output || call.params) && (
            expanded
              ? <ChevronDown className="w-3 h-3 opacity-50" />
              : <ChevronRight className="w-3 h-3 opacity-50" />
          )}
        </div>
      </button>

      {/* Expandable output */}
      <AnimatePresence>
        {expanded && (call.output || call.params) && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-2 space-y-1.5">
              {/* Params */}
              {call.params && (
                <div className="relative">
                  <div className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-0.5">
                    Parameters
                  </div>
                  <pre className="text-[11px] bg-black/5 dark:bg-white/5 rounded p-2 overflow-x-auto max-h-32 font-mono whitespace-pre-wrap break-all">
                    {typeof call.params === 'string' ? call.params : JSON.stringify(call.params, null, 2)}
                  </pre>
                </div>
              )}

              {/* Output */}
              {call.output && (
                <div className="relative">
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                      Output
                    </span>
                    <button
                      onClick={() => handleCopy(call.output!)}
                      className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                    >
                      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                  <pre className="text-[11px] bg-black/5 dark:bg-white/5 rounded p-2 overflow-x-auto max-h-48 font-mono whitespace-pre-wrap break-all">
                    {call.output}
                  </pre>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

// ── Main Panel ────────────────────────────────────────────────

export function ToolActivityPanel({ toolCalls, className, compact }: ToolActivityPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Auto-scroll to latest
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [toolCalls.length, autoScroll])

  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 50)
  }

  const runningCount = toolCalls.filter(t => t.status === 'running').length
  const doneCount = toolCalls.filter(t => t.status === 'done').length
  const failedCount = toolCalls.filter(t => t.status === 'failed').length

  if (toolCalls.length === 0) {
    return (
      <div className={cn('flex flex-col items-center justify-center py-8 text-center', className)}>
        <Wrench className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
        <p className="text-[12px] text-gray-400 dark:text-gray-500">
          No tool activity yet
        </p>
        <p className="text-[11px] text-gray-300 dark:text-gray-600 mt-1">
          Tool calls will appear here when the agent uses tools
        </p>
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col h-full', className)}>
      {/* Header stats */}
      <div className="flex items-center gap-3 px-3 py-2 border-b border-gray-200 dark:border-[#2a2a45] shrink-0">
        <div className="flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5 text-blue-500" />
          <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
            Tool Activity
          </span>
        </div>
        <div className="flex items-center gap-2 ml-auto text-[10px]">
          {runningCount > 0 && (
            <span className="flex items-center gap-1 text-blue-500">
              <Loader2 className="w-3 h-3 animate-spin" /> {runningCount}
            </span>
          )}
          {doneCount > 0 && (
            <span className="flex items-center gap-1 text-emerald-600">
              <CheckCircle2 className="w-3 h-3" /> {doneCount}
            </span>
          )}
          {failedCount > 0 && (
            <span className="flex items-center gap-1 text-red-500">
              <XCircle className="w-3 h-3" /> {failedCount}
            </span>
          )}
        </div>
      </div>

      {/* Tool calls list */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-2 space-y-1.5"
      >
        <AnimatePresence mode="popLayout">
          {toolCalls.map(call => (
            <ToolCallItem key={call.id} call={call} compact={compact} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
