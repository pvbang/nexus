// ============================================================
// MekongAI Nexus - AgentCommunicationFeed
// Group chat view displaying inter-agent messages
// ============================================================

import { useEffect, useRef, useState, useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowRight,
  CheckCircle,
  HelpCircle,
  Megaphone,
  MessageCircle,
  ChevronDown,
  Users,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useProcessStore } from '../../stores/processStore'
import { EmptyState } from '../ui/EmptyState'
import type { AgentMessage } from '../../types/process'

// ── Role → color mapping ──────────────────────────────────────

const ROLE_COLORS: Record<string, { bg: string; text: string; ring: string }> =
  {
    researcher: {
      bg: 'bg-blue-100 dark:bg-blue-900/30',
      text: 'text-blue-700 dark:text-blue-400',
      ring: 'ring-blue-400/30',
    },
    coder: {
      bg: 'bg-emerald-100 dark:bg-emerald-900/30',
      text: 'text-emerald-700 dark:text-emerald-400',
      ring: 'ring-emerald-400/30',
    },
    analyst: {
      bg: 'bg-purple-100 dark:bg-purple-900/30',
      text: 'text-purple-700 dark:text-purple-400',
      ring: 'ring-purple-400/30',
    },
    writer: {
      bg: 'bg-orange-100 dark:bg-orange-900/30',
      text: 'text-orange-700 dark:text-orange-400',
      ring: 'ring-orange-400/30',
    },
    judge: {
      bg: 'bg-red-100 dark:bg-red-900/30',
      text: 'text-red-700 dark:text-red-400',
      ring: 'ring-red-400/30',
    },
  }

const DEFAULT_ROLE_COLOR = {
  bg: 'bg-gray-100 dark:bg-gray-800',
  text: 'text-gray-700 dark:text-gray-300',
  ring: 'ring-gray-400/30',
}

function getRoleColor(role: string) {
  return ROLE_COLORS[(role || '').toLowerCase()] ?? DEFAULT_ROLE_COLOR
}

// ── Message type icon ─────────────────────────────────────────

function MessageTypeIcon({ type }: { type: AgentMessage['type'] }) {
  const base = 'h-3.5 w-3.5 shrink-0'
  switch (type) {
    case 'delegate':
      return <ArrowRight className={cn(base, 'text-amber-500')} aria-label="Delegation" />
    case 'response':
      return <CheckCircle className={cn(base, 'text-emerald-500')} aria-label="Response" />
    case 'question':
      return <HelpCircle className={cn(base, 'text-purple-500')} aria-label="Question" />
    case 'broadcast':
      return <Megaphone className={cn(base, 'text-brand-500')} aria-label="Broadcast" />
    default:
      return null
  }
}

// ── Format timestamp ──────────────────────────────────────────

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// ── Single message bubble ─────────────────────────────────────

const COLLAPSE_THRESHOLD = 200

function MessageBubble({ msg }: { msg: AgentMessage }) {
  const [expanded, setExpanded] = useState(false)
  const roleColor = getRoleColor(msg.fromAgent.role)
  const isLong = msg.content.length > COLLAPSE_THRESHOLD
  const isBroadcast = msg.type === 'broadcast'

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.2 }}
      className={cn(
        'flex gap-2.5 px-3 py-2 rounded-lg transition-colors',
        isBroadcast
          ? 'bg-brand-50/60 dark:bg-brand-900/20 border border-brand-200/50 dark:border-brand-800/30'
          : 'hover:bg-gray-50 dark:hover:bg-white/[0.02]',
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-2',
          roleColor.bg,
          roleColor.text,
          roleColor.ring,
        )}
        title={`${msg.fromAgent.name} (${msg.fromAgent.role})`}
      >
        {msg.fromAgent.name.charAt(0).toUpperCase()}
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Header */}
        <div className="flex items-center gap-2 mb-0.5">
          <span className={cn('text-xs font-semibold', roleColor.text)}>
            {msg.fromAgent.name}
          </span>
          <span className="text-[10px] text-text-muted dark:text-gray-500 capitalize">
            {msg.fromAgent.role}
          </span>
          <MessageTypeIcon type={msg.type} />
          {msg.type === 'delegate' && msg.toAgent && (
            <span className="text-[10px] text-amber-600 dark:text-amber-400">
              → {msg.toAgent.name}
            </span>
          )}
          <span className="ml-auto text-[10px] text-text-muted dark:text-gray-600 tabular-nums">
            {formatTime(msg.timestamp)}
          </span>
        </div>

        {/* Body */}
        <p className="text-sm leading-relaxed text-text-primary dark:text-gray-200 whitespace-pre-wrap break-words">
          {isLong && !expanded
            ? msg.content.slice(0, COLLAPSE_THRESHOLD) + '…'
            : msg.content}
        </p>

        {/* Expand/collapse toggle */}
        {isLong && (
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            className={cn(
              'mt-1 inline-flex items-center gap-0.5 text-xs font-medium',
              'text-brand-500 hover:text-brand-600 dark:text-brand-400 dark:hover:text-brand-300',
              'transition-colors',
            )}
          >
            {expanded ? 'Show less' : 'Show more'}
            <ChevronDown
              className={cn(
                'h-3 w-3 transition-transform duration-200',
                expanded && 'rotate-180',
              )}
              aria-hidden
            />
          </button>
        )}
      </div>
    </motion.div>
  )
}

// ── Main component ────────────────────────────────────────────

export function AgentCommunicationFeed() {
  const agentMessages = useProcessStore((s) => s.agentMessages)
  const feedRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    const el = feedRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [agentMessages.length])

  // Count unique active agents
  const activeAgentCount = useMemo(() => {
    const ids = new Set(agentMessages.map((m) => m.fromAgent.id))
    return ids.size
  }, [agentMessages])

  if (agentMessages.length === 0) {
    return (
      <EmptyState
        icon={<MessageCircle className="h-10 w-10" />}
        title="No agent messages yet"
        description="Agent communications will appear here as they collaborate."
      />
    )
  }

  return (
    <div className="flex flex-col h-full animate-fade-in">
      {/* Header */}
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2 border-b',
          'border-border dark:border-[#2a2a45]',
        )}
      >
        <Users className="h-4 w-4 text-brand-500 dark:text-brand-400" aria-hidden />
        <span className="text-xs font-medium text-text-secondary dark:text-gray-300">
          {activeAgentCount} agent{activeAgentCount !== 1 ? 's' : ''} active
        </span>
        <span className="text-xs text-text-muted dark:text-gray-500">
          · {agentMessages.length} message{agentMessages.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Messages feed */}
      <div
        ref={feedRef}
        className="flex-1 overflow-y-auto space-y-1 py-2 scroll-smooth"
      >
        <AnimatePresence initial={false}>
          {agentMessages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
