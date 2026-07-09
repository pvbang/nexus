// ============================================================
// MekongAI Nexus - ToolCreationCard
// Inline chat card showing dynamic tool creation events
// ============================================================

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Zap, ChevronDown, ChevronUp, CheckCircle2, XCircle } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { Badge } from '../../ui/Badge'
import { CodeBlock } from '../../ui/CodeBlock'
import { Spinner } from '../../ui/Spinner'
import type { ToolCreationEvent } from '../../../types/process'

interface ToolCreationCardProps {
  event: ToolCreationEvent
}

const STATUS_CONFIG: Record<ToolCreationEvent['status'], {
  label: string
  variant: 'info' | 'warning' | 'success' | 'error'
}> = {
  creating: { label: 'Creating…', variant: 'info' },
  testing: { label: 'Testing…', variant: 'warning' },
  ready: { label: 'Ready', variant: 'success' },
  failed: { label: 'Failed', variant: 'error' },
}

export function ToolCreationCard({ event }: ToolCreationCardProps) {
  const [showCode, setShowCode] = useState(false)
  const config = STATUS_CONFIG[event.status]
  const isInProgress = event.status === 'creating' || event.status === 'testing'

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 360, damping: 26, mass: 0.8 }}
      className={cn(
        'rounded-lg border border-border bg-white px-3.5 py-2.5 text-sm',
        'dark:bg-[#1a1a2e] dark:border-[#2a2a45]',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <Zap className="h-4 w-4 text-amber-500 dark:text-amber-400 shrink-0" />
        <span className="font-semibold text-text-primary dark:text-gray-100">
          New Tool Created
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {isInProgress && <Spinner size="xs" className="text-blue-500" />}
          {event.status === 'ready' && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {event.status === 'failed' && <XCircle className="h-4 w-4 text-red-500" />}
          <Badge variant={config.variant} size="sm">
            {config.label}
          </Badge>
        </span>
      </div>

      {/* Tool name */}
      <div className="mt-1.5 text-xs">
        <span className="text-text-muted dark:text-gray-500">Name: </span>
        <span className="font-mono font-medium text-text-primary dark:text-gray-200">
          {event.toolName}
        </span>
      </div>

      {/* Reason */}
      {event.reason && (
        <p className="mt-1 text-xs text-text-secondary dark:text-gray-400 italic">
          &quot;{event.reason}&quot;
        </p>
      )}

      {/* Code toggle */}
      {event.code && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setShowCode((p) => !p)}
            className="flex items-center gap-1 text-xs text-brand-600 dark:text-brand-400 hover:underline"
          >
            {showCode ? (
              <>
                <ChevronUp className="h-3 w-3" />
                Hide code
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
                View code
              </>
            )}
          </button>

          {showCode && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              transition={{ duration: 0.2 }}
              className="mt-1.5 overflow-hidden"
            >
              <CodeBlock
                code={event.code}
                language="python"
                maxHeight="200px"
                showLineNumbers
              />
            </motion.div>
          )}
        </div>
      )}
    </motion.div>
  )
}
