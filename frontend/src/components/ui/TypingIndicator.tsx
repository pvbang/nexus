// ============================================================
// MekongAI Nexus - Enhanced Typing Indicator
// Shows agent thinking/typing status with smooth animations
// ============================================================

import { motion } from 'framer-motion'
import { Sparkles, Brain, Loader2 } from 'lucide-react'
import { cn } from '../../lib/cn'

export type TypingMode = 'thinking' | 'typing' | 'analyzing' | 'searching'

interface TypingIndicatorProps {
  mode?: TypingMode
  agentName?: string
  className?: string
}

const modeConfig: Record<TypingMode, { label: string; icon: typeof Brain; color: string }> = {
  thinking: { label: 'Thinking', icon: Brain, color: 'text-purple-500' },
  typing: { label: 'Typing', icon: Sparkles, color: 'text-blue-500' },
  analyzing: { label: 'Analyzing', icon: Loader2, color: 'text-amber-500' },
  searching: { label: 'Searching', icon: Loader2, color: 'text-emerald-500' },
}

export function TypingIndicator({ mode = 'thinking', agentName, className }: TypingIndicatorProps) {
  const config = modeConfig[mode]
  const Icon = config.icon

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -5, scale: 0.95 }}
      transition={{ duration: 0.2 }}
      className={cn(
        'flex items-center gap-2.5 px-4 py-2.5 rounded-2xl rounded-bl-sm',
        'bg-gray-50 dark:bg-surface-secondary border border-gray-100 dark:border-border-subtle',
        'max-w-[280px]',
        className
      )}
    >
      {/* Agent avatar */}
      <div className={cn(
        'w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600',
        'flex items-center justify-center shrink-0'
      )}>
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <Icon className={cn('w-3 h-3', config.color, mode !== 'typing' && 'animate-spin')} />
          <span className={cn('text-[11px] font-medium', config.color)}>
            {agentName ? `${agentName} — ` : ''}{config.label}
          </span>
        </div>
        {/* Animated dots */}
        <div className="flex items-center gap-1 mt-1">
          {[0, 1, 2].map(i => (
            <motion.div
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-gray-600"
              animate={{
                scale: [1, 1.4, 1],
                opacity: [0.5, 1, 0.5],
              }}
              transition={{
                duration: 1.4,
                repeat: Infinity,
                delay: i * 0.2,
                ease: 'easeInOut',
              }}
            />
          ))}
        </div>
      </div>
    </motion.div>
  )
}

// ── Minimal inline variant ─────────────────────────────────────

export function TypingIndicatorInline({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-0.5', className)}>
      {[0, 1, 2].map(i => (
        <motion.span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-gray-400 dark:bg-gray-500"
          animate={{
            y: [0, -3, 0],
            opacity: [0.4, 1, 0.4],
          }}
          transition={{
            duration: 0.8,
            repeat: Infinity,
            delay: i * 0.15,
          }}
        />
      ))}
    </span>
  )
}
