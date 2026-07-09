// ============================================================
// MekongAI Nexus - AgentThinkingCard
// Displays agent reasoning/thinking process inline with full
// content visibility, markdown rendering, and decision markers.
// Enhanced: no truncation by default, collapsible for very long content.
// ============================================================

import { useState } from 'react'
import { Brain, ChevronDown, ChevronRight, Lightbulb, Target, ClipboardList, Maximize2, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { cn } from '../../../lib/cn'
import { MarkdownRenderer } from '../../ui/MarkdownRenderer'

// ── Props ─────────────────────────────────────────────────────

interface AgentThinkingCardProps {
  agentName: string
  agentRole: string
  content: string
  timestamp?: number
  isActive?: boolean
}

// ── Helpers ───────────────────────────────────────────────────

function formatRelativeTime(timestamp: number): string {
  const seconds = Math.floor((Date.now() - timestamp) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

/** Detect thinking section type from content for visual indicators */
function detectThinkingType(content: string): {
  icon: typeof Brain
  label: string
  accentColor: string
} {
  const lower = (content || '').toLowerCase()
  if (lower.includes('plan') || lower.includes('step') || lower.includes('approach')) {
    return { icon: ClipboardList, label: 'Planning', accentColor: 'text-blue-500' }
  }
  if (lower.includes('decision') || lower.includes('choose') || lower.includes('select') || lower.includes('quyết định')) {
    return { icon: Target, label: 'Decision', accentColor: 'text-amber-500' }
  }
  if (lower.includes('analyz') || lower.includes('phân tích') || lower.includes('consider') || lower.includes('evaluat')) {
    return { icon: Lightbulb, label: 'Analysis', accentColor: 'text-emerald-500' }
  }
  return { icon: Brain, label: 'Thinking', accentColor: 'text-purple-500' }
}

// ── Fullscreen Modal ──────────────────────────────────────────

function ThinkingFullscreenModal({
  agentName,
  agentRole,
  content,
  onClose,
}: {
  agentName: string
  agentRole: string
  content: string
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 8 }}
        transition={{ duration: 0.15 }}
        className={cn(
          'relative z-10 w-full max-w-3xl max-h-[85vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden',
          'bg-white dark:bg-[#0e0e20] border border-purple-200 dark:border-purple-800',
        )}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-purple-100 dark:border-purple-900 shrink-0 bg-gradient-to-r from-purple-50 to-indigo-50 dark:from-purple-900/30 dark:to-indigo-900/30">
          <Brain className="h-4 w-4 text-purple-500 shrink-0" />
          <span className="text-sm font-bold text-purple-900 dark:text-purple-200">
            {agentName}
          </span>
          {agentRole && (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-purple-100 text-purple-700 dark:bg-purple-800/50 dark:text-purple-300">
              {agentRole}
            </span>
          )}
          <span className="text-[10px] text-purple-400 ml-1">
            {content.length.toLocaleString()} chars
          </span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto p-1.5 rounded-lg hover:bg-purple-100 dark:hover:bg-purple-900/50 text-purple-400 hover:text-purple-700 dark:hover:text-purple-200 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          <div className="prose-nexus text-[13px] leading-[1.7] text-purple-900 dark:text-purple-100">
            <MarkdownRenderer content={content} breaks />
          </div>
        </div>
      </motion.div>
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────

export function AgentThinkingCard({
  agentName,
  agentRole,
  content,
  timestamp,
  isActive = false,
}: AgentThinkingCardProps) {
  // For very long content (>600 chars), start collapsed; otherwise expanded
  const isLong = content.length > 600
  const [expanded, setExpanded] = useState(!isLong)
  const [showFullscreen, setShowFullscreen] = useState(false)

  const thinkingType = detectThinkingType(content)
  const ThinkingIcon = thinkingType.icon

  // Preview: show first 400 chars when collapsed
  const previewContent = isLong && !expanded ? content.slice(0, 400) + '…' : content

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 14, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{
          type: 'spring',
          stiffness: 350,
          damping: 26,
          mass: 0.8,
        }}
        className={cn(
          'relative rounded-xl border overflow-hidden transition-colors duration-200',
          'bg-gradient-to-r from-purple-50/80 to-indigo-50/80',
          'border-purple-200/80',
          'dark:from-purple-900/20 dark:to-indigo-900/20',
          'dark:border-purple-800/60',
          isActive && 'shadow-md shadow-purple-500/10 dark:shadow-purple-500/20',
        )}
      >
        {/* Left accent bar with gradient animation */}
        <motion.div
          className="absolute left-0 top-0 bottom-0 w-[3px] bg-gradient-to-b from-purple-400 via-purple-600 to-indigo-500"
          animate={isActive ? {
            backgroundPosition: ['0% 0%', '0% 100%', '0% 0%'],
            opacity: [0.8, 1, 0.8],
          } : { opacity: 1 }}
          transition={isActive ? {
            duration: 2,
            repeat: Infinity,
            ease: 'easeInOut',
          } : {}}
        />

        <div className="pl-4 pr-3 py-2.5">
          {/* Header */}
          <div className="flex items-center gap-2">
            <ThinkingIcon className={cn('h-3.5 w-3.5 shrink-0', thinkingType.accentColor)} />
            <span className="text-[13px] font-semibold text-purple-900 dark:text-purple-200 truncate">
              {agentName}
            </span>
            {agentRole && (
              <span
                className={cn(
                  'inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium leading-none',
                  'bg-purple-100 text-purple-700',
                  'dark:bg-purple-800/50 dark:text-purple-300',
                )}
              >
                {agentRole}
              </span>
            )}
            <span
              className={cn(
                'inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold leading-none',
                thinkingType.accentColor,
                'bg-white/60 dark:bg-white/10',
              )}
            >
              {thinkingType.label}
            </span>
            <div className="ml-auto flex items-center gap-1">
              {timestamp != null && (
                <span className="text-[10px] text-purple-400 dark:text-purple-500 shrink-0">
                  {formatRelativeTime(timestamp)}
                </span>
              )}
              {isLong && (
                <button
                  type="button"
                  onClick={() => setShowFullscreen(true)}
                  className="p-1 rounded hover:bg-purple-100 dark:hover:bg-purple-900/50 text-purple-400 hover:text-purple-700 dark:hover:text-purple-200 transition-colors"
                  title="View fullscreen"
                >
                  <Maximize2 className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>

          {/* Content */}
          <div className="mt-1.5">
            {isLong ? (
              <button
                type="button"
                onClick={() => setExpanded((p) => !p)}
                className="flex items-start gap-1 w-full text-left group"
              >
                {expanded ? (
                  <ChevronDown className="h-3 w-3 text-purple-400 mt-1 shrink-0" />
                ) : (
                  <ChevronRight className="h-3 w-3 text-purple-400 mt-1 shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="prose-nexus text-[12px] leading-[1.7] text-purple-800 dark:text-purple-200/80">
                    <MarkdownRenderer content={previewContent} breaks />
                  </div>
                  {!expanded && (
                    <span className="text-[10px] text-purple-500 dark:text-purple-400 hover:underline mt-0.5 inline-block">
                      Show full reasoning ({content.length.toLocaleString()} chars)
                    </span>
                  )}
                </div>
              </button>
            ) : (
              <div className="prose-nexus text-[12px] leading-[1.7] text-purple-800 dark:text-purple-200/80 pl-4">
                <MarkdownRenderer content={content} breaks />
              </div>
            )}
          </div>
        </div>
      </motion.div>

      {/* Fullscreen modal */}
      <AnimatePresence>
        {showFullscreen && (
          <ThinkingFullscreenModal
            agentName={agentName}
            agentRole={agentRole}
            content={content}
            onClose={() => setShowFullscreen(false)}
          />
        )}
      </AnimatePresence>
    </>
  )
}
