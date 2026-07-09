// ============================================================
// MekongAI Nexus - AgentCommunicationCard
// Displays agent-to-agent communication messages inline
// Now with markdown rendering for debate content.
// ============================================================

import { useState } from 'react'
import { MessageSquare, ArrowRight, ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { MarkdownRenderer } from '../../ui/MarkdownRenderer'
import type { AgentMessage } from '../../../types/process'

// ── Props ─────────────────────────────────────────────────────

interface AgentCommunicationCardProps {
  message: AgentMessage
}

// ── Helpers ───────────────────────────────────────────────────

/** Deterministic color for agent avatars based on role */
function getAvatarColor(role: string): string {
  const colors = [
    'bg-blue-500',
    'bg-emerald-500',
    'bg-amber-500',
    'bg-rose-500',
    'bg-violet-500',
    'bg-cyan-500',
    'bg-orange-500',
    'bg-teal-500',
  ]
  let hash = 0
  for (let i = 0; i < role.length; i++) {
    hash = role.charCodeAt(i) + ((hash << 5) - hash)
  }
  return colors[Math.abs(hash) % colors.length]
}

/** Badge style per message type */
const TYPE_BADGE_STYLES: Record<AgentMessage['type'], string> = {
  message: 'bg-gray-100 text-gray-600 dark:bg-gray-700/50 dark:text-gray-300',
  delegate: 'bg-blue-100 text-blue-700 dark:bg-blue-800/50 dark:text-blue-300',
  response: 'bg-green-100 text-green-700 dark:bg-green-800/50 dark:text-green-300',
  question: 'bg-amber-100 text-amber-700 dark:bg-amber-800/50 dark:text-amber-300',
  broadcast: 'bg-purple-100 text-purple-700 dark:bg-purple-800/50 dark:text-purple-300',
}

function formatRelativeTime(timestamp: number): string {
  const seconds = Math.floor((Date.now() - timestamp) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

// ── Avatar ────────────────────────────────────────────────────

function AgentAvatar({ name, role }: { name: string; role: string }) {
  const initial = name.charAt(0).toUpperCase()
  const colorClass = getAvatarColor(role)

  return (
    <div
      className={cn(
        'w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0',
        colorClass,
      )}
      title={`${name} (${role})`}
    >
      {initial}
    </div>
  )
}

// ── Component ─────────────────────────────────────────────────

export function AgentCommunicationCard({ message }: AgentCommunicationCardProps) {
  const [expanded, setExpanded] = useState(false)

  const shouldTruncate = message.content.length > 300
  const previewContent = shouldTruncate ? message.content.slice(0, 250) + '…' : message.content
  const isBroadcast = message.toAgent == null

  return (
    <div
      className={cn(
        'rounded-xl border overflow-hidden transition-colors duration-200',
        'bg-blue-50 border-blue-200',
        'dark:bg-blue-900/20 dark:border-blue-800',
      )}
    >
      <div className="px-3 py-2.5">
        {/* Header: from → to + type badge */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <AgentAvatar name={message.fromAgent.name} role={message.fromAgent.role} />
          <span className="text-[12px] font-semibold text-blue-900 dark:text-blue-200 truncate max-w-[100px]">
            {message.fromAgent.name}
          </span>

          <ArrowRight className="h-3 w-3 text-blue-400 dark:text-blue-500 shrink-0" />

          {isBroadcast ? (
            <span className="text-[12px] font-medium text-purple-600 dark:text-purple-400 italic">
              Broadcast
            </span>
          ) : (
            <>
              <AgentAvatar name={message.toAgent!.name} role={message.toAgent!.role} />
              <span className="text-[12px] font-semibold text-blue-900 dark:text-blue-200 truncate max-w-[100px]">
                {message.toAgent!.name}
              </span>
            </>
          )}

          {/* Type badge */}
          <span
            className={cn(
              'inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium leading-none ml-auto',
              TYPE_BADGE_STYLES[message.type],
            )}
          >
            {message.type}
          </span>
        </div>

        {/* Content — with Markdown rendering for debate messages */}
        <div className="mt-2">
          {shouldTruncate ? (
            <button
              type="button"
              onClick={() => setExpanded((p) => !p)}
              className="flex items-start gap-1 w-full text-left group"
            >
              {expanded ? (
                <ChevronDown className="h-3 w-3 text-blue-400 mt-0.5 shrink-0" />
              ) : (
                <ChevronRight className="h-3 w-3 text-blue-400 mt-0.5 shrink-0" />
              )}
              {expanded ? (
                <MarkdownRenderer
                  content={message.content}
                  className="text-[12px] leading-[1.6] text-blue-800 dark:text-blue-200/80 prose-headings:text-blue-900 dark:prose-headings:text-blue-100 max-w-none"
                />
              ) : (
                <p
                  className={cn(
                    'text-[12px] leading-[1.6] text-blue-800 dark:text-blue-200/80',
                    'line-clamp-3',
                  )}
                >
                  {previewContent}
                </p>
              )}
            </button>
          ) : (
            <div className="flex items-start gap-1.5">
              <MessageSquare className="h-3 w-3 text-blue-400 mt-0.5 shrink-0" />
              <MarkdownRenderer
                content={message.content}
                className="text-[12px] leading-[1.6] text-blue-800 dark:text-blue-200/80 prose-headings:text-blue-900 dark:prose-headings:text-blue-100 max-w-none"
              />
            </div>
          )}
        </div>

        {/* Timestamp */}
        <div className="mt-1.5 flex justify-end">
          <span className="text-[10px] text-blue-400 dark:text-blue-500">
            {formatRelativeTime(message.timestamp)}
          </span>
        </div>
      </div>
    </div>
  )
}
