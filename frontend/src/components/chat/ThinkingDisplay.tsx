// ============================================================
// Nexus - ThinkingDisplay
// Displays LLM reasoning/thinking content with 3 view modes:
//   Hidden → Collapsed (default) → Expanded
// ============================================================

import { useRef, useEffect, useCallback } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Brain, ChevronDown, ChevronUp, EyeOff, Maximize2 } from 'lucide-react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import { Badge } from '../ui/Badge'
import { usePreferenceStore, type ThinkingDisplayMode } from '../../stores/preferenceStore'

// ── Props ─────────────────────────────────────────────────────

export interface ThinkingDisplayProps {
  /** Reasoning/thinking content text */
  thinking: string
  /** Token count for thinking */
  thinkingTokens?: number
  /** True when still receiving thinking content */
  isStreaming?: boolean
}

// ── Mode cycle order ──────────────────────────────────────────

const MODE_CYCLE: ThinkingDisplayMode[] = ['hidden', 'collapsed', 'expanded']

function nextMode(current: ThinkingDisplayMode): ThinkingDisplayMode {
  const idx = MODE_CYCLE.indexOf(current)
  return MODE_CYCLE[(idx + 1) % MODE_CYCLE.length]
}

// ── Mode icon component ───────────────────────────────────────

function ModeIcon({ mode }: { mode: ThinkingDisplayMode }) {
  switch (mode) {
    case 'hidden':
      return <EyeOff className="w-3 h-3" />
    case 'collapsed':
      return <ChevronDown className="w-3 h-3" />
    case 'expanded':
      return <Maximize2 className="w-3 h-3" />
  }
}

// ── Streaming indicator ───────────────────────────────────────

function ThinkingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 ml-1">
      <span className="w-1 h-1 rounded-full bg-purple-400 animate-pulse" />
      <span className="w-1 h-1 rounded-full bg-purple-400 animate-pulse [animation-delay:150ms]" />
      <span className="w-1 h-1 rounded-full bg-purple-400 animate-pulse [animation-delay:300ms]" />
    </span>
  )
}

// ── Token count formatter ─────────────────────────────────────

function formatTokenCount(tokens: number): string {
  if (tokens >= 1000) {
    return `${(tokens / 1000).toFixed(1)}k`
  }
  return String(tokens)
}

// ── Component ─────────────────────────────────────────────────

export function ThinkingDisplay({
  thinking,
  thinkingTokens,
  isStreaming = false,
}: ThinkingDisplayProps) {
  const mode = usePreferenceStore((s) => s.thinkingDisplayMode)
  const setMode = usePreferenceStore((s) => s.setThinkingDisplayMode)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when streaming in expanded mode
  useEffect(() => {
    if (isStreaming && mode === 'expanded' && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [thinking, isStreaming, mode])

  const handleCycleMode = useCallback(() => {
    setMode(nextMode(mode))
  }, [mode, setMode])

  const handleExpand = useCallback(() => {
    setMode('expanded')
  }, [setMode])

  const handleCollapse = useCallback(() => {
    setMode('collapsed')
  }, [setMode])

  // Token badge content
  const tokenLabel = thinkingTokens
    ? `${formatTokenCount(thinkingTokens)} tokens`
    : null

  // ── Hidden mode ───────────────────────────────────────────
  if (mode === 'hidden') {
    return (
      <button
        type="button"
        onClick={handleCycleMode}
        className={cn(
          'flex items-center gap-1.5 px-2 py-1 mb-2 rounded-lg',
          'text-[12px] text-purple-600 dark:text-purple-400',
          'hover:bg-purple-50 dark:hover:bg-purple-950/40',
          'transition-colors duration-150 cursor-pointer',
        )}
        title="Show thinking content"
      >
        <Brain className="w-3.5 h-3.5" />
        <span>Thinking</span>
        {tokenLabel && (
          <span className="text-purple-400 dark:text-purple-500">
            ({tokenLabel})
          </span>
        )}
        {isStreaming && <ThinkingIndicator />}
      </button>
    )
  }

  // ── Collapsed / Expanded modes ────────────────────────────
  const isExpanded = mode === 'expanded'

  return (
    <div
      className={cn(
        'mb-3 rounded-comfortable overflow-hidden',
        'bg-purple-50/70 dark:bg-purple-950/30',
        'border-l-[3px]',
        isExpanded
          ? 'border-l-purple-500 dark:border-l-purple-500'
          : 'border-l-purple-400 dark:border-l-purple-600',
      )}
    >
      {/* Header */}
      <div
        className={cn(
          'flex items-center justify-between gap-2 px-3 py-2',
          'cursor-pointer select-none',
          'hover:bg-purple-100/50 dark:hover:bg-purple-900/20',
          'transition-colors duration-150',
        )}
        onClick={isExpanded ? handleCollapse : handleExpand}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            isExpanded ? handleCollapse() : handleExpand()
          }
        }}
        aria-expanded={isExpanded}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Brain className="w-4 h-4 text-purple-500 dark:text-purple-400 shrink-0" />
          <span className="text-[13px] font-medium text-purple-700 dark:text-purple-300">
            Thinking
          </span>
          {tokenLabel && (
            <Badge variant="purple" size="sm">
              {tokenLabel}
            </Badge>
          )}
          {isStreaming && <ThinkingIndicator />}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {/* Mode cycle button */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              handleCycleMode()
            }}
            className={cn(
              'p-1 rounded-md',
              'text-purple-400 hover:text-purple-600',
              'dark:text-purple-500 dark:hover:text-purple-300',
              'hover:bg-purple-100 dark:hover:bg-purple-900/30',
              'transition-colors duration-150',
            )}
            title={`Switch to ${nextMode(mode)} mode`}
            aria-label={`Switch thinking display to ${nextMode(mode)} mode`}
          >
            <ModeIcon mode={nextMode(mode)} />
          </button>

          {/* Expand/collapse chevron */}
          {isExpanded ? (
            <ChevronUp className="w-4 h-4 text-purple-400 dark:text-purple-500" />
          ) : (
            <ChevronDown className="w-4 h-4 text-purple-400 dark:text-purple-500" />
          )}
        </div>
      </div>

      {/* Content area */}
      <AnimatePresence initial={false}>
        {/* Collapsed: preview with gradient fade */}
        {!isExpanded && (
          <motion.div
            key="collapsed-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeInOut' }}
          >
            <div className="relative px-3 pb-3">
              <div className="max-h-[4.5em] overflow-hidden">
                <div className="text-[13px] leading-[1.5] text-purple-800/70 dark:text-purple-300/70 italic">
                  {thinking.slice(0, 300)}
                  {thinking.length > 300 && '…'}
                </div>
              </div>
              {/* Gradient fade overlay */}
              <div
                className={cn(
                  'absolute bottom-3 left-3 right-3 h-8',
                  'bg-gradient-to-t from-purple-50/70 to-transparent',
                  'dark:from-purple-950/30 dark:to-transparent',
                  'pointer-events-none',
                )}
              />
              {/* Show more button */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  handleExpand()
                }}
                className={cn(
                  'relative z-10 mt-1 text-[11px] font-medium',
                  'text-purple-500 hover:text-purple-700',
                  'dark:text-purple-400 dark:hover:text-purple-200',
                  'transition-colors duration-150',
                )}
              >
                Show more ↓
              </button>
            </div>
          </motion.div>
        )}

        {/* Expanded: full content */}
        {isExpanded && (
          <motion.div
            key="expanded-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
          >
            <div
              ref={scrollRef}
              className={cn(
                'px-3 pb-3 overflow-y-auto',
                'max-h-[400px]',
                'scrollbar-thin scrollbar-thumb-purple-300 dark:scrollbar-thumb-purple-700',
              )}
            >
              <div className="text-sm italic text-purple-800/80 dark:text-purple-300/80">
                <MarkdownRenderer
                  content={thinking}
                  className="!text-purple-800/80 dark:!text-purple-300/80 [&_strong]:!text-purple-900 dark:[&_strong]:!text-purple-200"
                />
                {isStreaming && <ThinkingIndicator />}
              </div>
            </div>

            {/* Show less button */}
            <div className="px-3 pb-2">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  handleCollapse()
                }}
                className={cn(
                  'text-[11px] font-medium',
                  'text-purple-500 hover:text-purple-700',
                  'dark:text-purple-400 dark:hover:text-purple-200',
                  'transition-colors duration-150',
                )}
              >
                Show less ↑
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
