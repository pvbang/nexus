// ============================================================
// MekongAI Nexus - RevisionCard
// Displays agent revision after critique with markdown-rendered
// revised content and agent name subtitle.
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { PenLine, ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { sanitizeDebateContent } from '../../../lib/utils'
import { MarkdownRenderer } from '../../ui/MarkdownRenderer'
import type { RevisionResult } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface RevisionCardProps {
  data: RevisionResult
}

// ── Component ─────────────────────────────────────────────────

export function RevisionCard({ data }: RevisionCardProps) {
  const content = sanitizeDebateContent(data.revised_content ?? '')
  const isLong = content.length > 300
  const [expanded, setExpanded] = useState(!isLong)

  const previewContent = isLong && !expanded ? content.slice(0, 300) + '…' : content

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border overflow-hidden',
        'bg-gradient-to-r from-indigo-50/80 to-blue-50/80 border-indigo-200/80',
        'dark:from-indigo-900/20 dark:to-blue-900/20 dark:border-indigo-800/60',
      )}
    >
      <div className="px-3.5 py-2.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <PenLine className="h-4 w-4 text-indigo-600 dark:text-indigo-400 shrink-0" />
          <span className="text-[13px] font-semibold text-gray-800 dark:text-gray-100">
            Revision
          </span>
          {data.agent_name && (
            <span className={cn(
              'inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium leading-none',
              'bg-indigo-100 text-indigo-700',
              'dark:bg-indigo-800/50 dark:text-indigo-300',
            )}>
              {data.agent_name}
            </span>
          )}
          {isLong && (
            <button
              type="button"
              onClick={() => setExpanded((p) => !p)}
              className="ml-auto p-1 rounded hover:bg-indigo-100 dark:hover:bg-indigo-900/40 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
              aria-label={expanded ? 'Collapse content' : 'Expand content'}
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>

        {/* Content */}
        {content && (
          <div className="mt-2">
            <div className="prose-nexus text-[12px] leading-[1.7] text-gray-700 dark:text-gray-300">
              <MarkdownRenderer content={previewContent} />
            </div>
            {isLong && !expanded && (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className="text-[10px] text-indigo-500 dark:text-indigo-400 hover:underline mt-1"
              >
                Show full revision ({content.length.toLocaleString()} chars)
              </button>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}
