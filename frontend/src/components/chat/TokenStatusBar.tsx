// ============================================================
// MekongAI Nexus - Token Status Bar
// Real-time token count + cost estimation for chat sessions
// ============================================================

import { useState, useMemo, useCallback } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { useSessionStore } from '../../stores/sessionStore'
import { Tooltip } from '../ui/Tooltip'
import { cn } from '../../lib/cn'
import type { ChatMessage } from '../../types/api'

// ── Cost lookup table (per 1K tokens) ─────────────────────────

const COST_PER_1K: Record<string, { input: number; output: number }> = {
  'gpt-4o': { input: 0.0025, output: 0.01 },
  'gpt-4o-mini': { input: 0.00015, output: 0.0006 },
  'claude-sonnet-4-20250514': { input: 0.003, output: 0.015 },
  'claude-haiku-3': { input: 0.00025, output: 0.00125 },
  'gemini-2.0-flash': { input: 0.000075, output: 0.0003 },
}

const DEFAULT_COST_PER_1K = { input: 0.002, output: 0.008 }

// ── Types ─────────────────────────────────────────────────────

interface TokenTotals {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  estimatedCost: number
  lastResponseTimeMs: number | null
  modelName: string | null
  messageCount: number
}

interface TokenStatusBarProps {
  sessionId: string
}

// ── Helpers ───────────────────────────────────────────────────

/** Format number with thousands separators */
function formatNumber(n: number): string {
  return n.toLocaleString('en-US')
}

/** Format cost to appropriate precision */
function formatCost(cost: number): string {
  if (cost === 0) return '$0.00'
  if (cost < 0.001) return `$${cost.toFixed(6)}`
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(4)}`
}

/** Format duration from ms */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Resolve model display name (shorten long names) */
function displayModel(model: string): string {
  // Map common long names to short labels
  const SHORT_NAMES: Record<string, string> = {
    'claude-sonnet-4-20250514': 'claude-sonnet-4',
    'claude-haiku-3': 'claude-haiku-3',
  }
  return SHORT_NAMES[model] ?? model
}

/** Calculate cost for given tokens using model-specific pricing */
function calculateCost(
  promptTokens: number,
  completionTokens: number,
  model: string | null,
): number {
  const pricing = (model ? COST_PER_1K[model] : undefined) ?? DEFAULT_COST_PER_1K
  return (
    (promptTokens / 1000) * pricing.input +
    (completionTokens / 1000) * pricing.output
  )
}

/** Aggregate token usage across all messages in a session */
function aggregateTokens(messages: ChatMessage[]): TokenTotals {
  let promptTokens = 0
  let completionTokens = 0
  let totalTokens = 0
  let lastResponseTimeMs: number | null = null
  let modelName: string | null = null
  let messageCount = 0

  for (const msg of messages) {
    if (msg.role !== 'assistant' || !msg.metadata) continue

    const usage = msg.metadata.usage
    if (usage) {
      promptTokens += usage.prompt_tokens
      completionTokens += usage.completion_tokens
      totalTokens += usage.total_tokens
      messageCount++
    }

    // Track latest response time and model
    const elapsedMs = msg.metadata.elapsed_ms as number | undefined
    if (typeof elapsedMs === 'number') {
      lastResponseTimeMs = elapsedMs
    }

    const model = msg.metadata.model as string | undefined
    if (typeof model === 'string' && model) {
      modelName = model
    }
  }

  const estimatedCost = calculateCost(promptTokens, completionTokens, modelName)

  return {
    promptTokens,
    completionTokens,
    totalTokens,
    estimatedCost,
    lastResponseTimeMs,
    modelName,
    messageCount,
  }
}

// ── Component ─────────────────────────────────────────────────

const EMPTY_MESSAGES: ChatMessage[] = []

export function TokenStatusBar({ sessionId }: TokenStatusBarProps) {
  const [expanded, setExpanded] = useState(false)

  const messagesMap = useSessionStore((s) => s.messagesBySession)
  const messages = messagesMap[sessionId] ?? EMPTY_MESSAGES

  const totals = useMemo(() => aggregateTokens(messages), [messages])

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => !prev)
  }, [])

  // Don't render if no token data at all
  if (totals.totalTokens === 0 && totals.messageCount === 0) {
    return null
  }

  const costBreakdown = totals.modelName
    ? `Input: ${formatNumber(totals.promptTokens)} × $${(COST_PER_1K[totals.modelName]?.input ?? DEFAULT_COST_PER_1K.input).toFixed(4)}/1K\nOutput: ${formatNumber(totals.completionTokens)} × $${(COST_PER_1K[totals.modelName]?.output ?? DEFAULT_COST_PER_1K.output).toFixed(4)}/1K`
    : `Input: ${formatNumber(totals.promptTokens)} tokens\nOutput: ${formatNumber(totals.completionTokens)} tokens`

  return (
    <div
      className={cn(
        'flex items-center justify-between px-3 sm:px-4',
        'h-8 min-h-[2rem]',
        'border-t border-border-light dark:border-[#2a2a45]',
        'bg-surface-secondary/60 dark:bg-[#12121f]/60',
        'backdrop-blur-sm',
        'select-none',
        'animate-fade-in',
      )}
    >
      {/* Left section: Token metrics */}
      <div className="flex items-center gap-1.5 sm:gap-3 overflow-hidden min-w-0">
        {/* Toggle button */}
        <button
          onClick={toggleExpanded}
          className={cn(
            'flex items-center gap-1 shrink-0',
            'text-micro text-text-muted dark:text-gray-500',
            'hover:text-text-secondary dark:hover:text-gray-400',
            'transition-colors duration-150',
          )}
          aria-label={expanded ? 'Collapse token details' : 'Expand token details'}
          aria-expanded={expanded}
        >
          <span className="text-[11px]">🪙</span>
          {expanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronUp className="w-3 h-3" />
          )}
        </button>

        {/* Collapsed view: total + cost (always visible) */}
        <span className="font-data text-micro text-text-muted dark:text-gray-500 tabular-nums shrink-0">
          <span className="font-medium text-text-secondary dark:text-gray-400 transition-[color] duration-300">
            {formatNumber(totals.totalTokens)}
          </span>
          {' tokens'}
        </span>

        {/* Expanded view: input/output breakdown (desktop only when expanded) */}
        {expanded && (
          <span className="hidden sm:inline-flex items-center gap-1.5 font-data text-micro text-text-muted dark:text-gray-500 tabular-nums animate-fade-in">
            <span className="text-border-dark dark:text-gray-600">·</span>
            <span>
              <span className="text-status-info/70">{formatNumber(totals.promptTokens)}</span>
              {' in'}
            </span>
            <span className="text-border-dark dark:text-gray-600">·</span>
            <span>
              <span className="text-status-success/70">{formatNumber(totals.completionTokens)}</span>
              {' out'}
            </span>
          </span>
        )}

        {/* Separator */}
        <span className="text-border-dark dark:text-gray-600 text-micro hidden sm:inline">│</span>

        {/* Cost */}
        <Tooltip content={costBreakdown} position="top" delay={300}>
          <span className="font-data text-micro text-text-muted dark:text-gray-500 tabular-nums shrink-0 cursor-help">
            <span className="text-[11px]">💰</span>
            {' '}
            <span className="font-medium text-text-secondary dark:text-gray-400">
              ~{formatCost(totals.estimatedCost)}
            </span>
          </span>
        </Tooltip>
      </div>

      {/* Right section: Response time + Model (desktop only) */}
      <div className="hidden sm:flex items-center gap-3 shrink-0">
        {/* Response time */}
        {totals.lastResponseTimeMs !== null && (
          <Tooltip content={`Last response: ${formatDuration(totals.lastResponseTimeMs)}`} position="top" delay={300}>
            <span className="font-data text-micro text-text-muted dark:text-gray-500 tabular-nums">
              <span className="text-[11px]">⏱</span>
              {' '}
              {formatDuration(totals.lastResponseTimeMs)}
            </span>
          </Tooltip>
        )}

        {/* Model */}
        {totals.modelName && (
          <>
            <span className="text-border-dark dark:text-gray-600 text-micro">│</span>
            <Tooltip content={`Model: ${totals.modelName}`} position="top" delay={300}>
              <span className="font-data text-micro text-text-muted dark:text-gray-500 truncate max-w-[140px]">
                <span className="text-[11px]">📊</span>
                {' '}
                {displayModel(totals.modelName)}
              </span>
            </Tooltip>
          </>
        )}
      </div>
    </div>
  )
}
