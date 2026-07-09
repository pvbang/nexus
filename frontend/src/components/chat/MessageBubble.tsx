// ============================================================
// MekongAI Nexus - MessageBubble
// Single chat message rendering (user, assistant, system)
// Extracted from ChatPanel.tsx lines 1023-1081
// Enhanced: Agent attribution display for assistant messages
// ============================================================

import { ExternalLink, Sparkles, Bot, Copy, Check } from 'lucide-react'
import React, { useState, useCallback } from 'react'
import { cn } from '../../lib/cn'
import { MarkdownRenderer } from '../ui/MarkdownRenderer'
import type { ChatMessage } from '../../types/api'
import type { SwarmRoundEvent } from '../../types/execution'
import { InlineDebateCard } from './InlineDebateCard'
import { ThinkingDisplay } from './ThinkingDisplay'

// ── Props ─────────────────────────────────────────────────────

export interface MessageBubbleProps {
  /** Chat message to render */
  message: ChatMessage
  /** Callback to retry a failed message (sets input to previous user msg) */
  onRetry?: (content: string) => void
}

// ── Metadata badge type ───────────────────────────────────────

interface MetadataBadge {
  label: string
  value: string
  color: string
  icon?: string
}

// ── Metadata renderer ─────────────────────────────────────────

function MessageMetadata({ message }: { message: ChatMessage }) {
  const meta = message.metadata
  if (!meta || message.role !== 'assistant') return null

  const badges: MetadataBadge[] = []

  if (meta.model) {
    badges.push({ label: 'Model', value: String(meta.model), color: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-900/30 dark:text-indigo-300 dark:border-indigo-800', icon: '🧠' })
  }
  if (meta.agents_spawned && meta.agents_spawned.length > 0) {
    badges.push({ label: 'Agents', value: String(meta.agents_spawned.length), color: 'bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800', icon: '🤖' })
  }
  if (meta.strategy) {
    badges.push({ label: 'Strategy', value: String(meta.strategy), color: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800', icon: '🎯' })
  }
  if (meta.tools_used && Array.isArray(meta.tools_used) && meta.tools_used.length > 0) {
    badges.push({ label: 'Tools', value: `${meta.tools_used.length} called`, color: 'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-900/30 dark:text-cyan-300 dark:border-cyan-800', icon: '🔧' })
  }
  if (meta.xskill) {
    // FIX 3.5: xskill can be an object — format it properly instead of String()
    const xskill = meta.xskill
    let xskillValue: string
    if (typeof xskill === 'object' && xskill !== null) {
      const parts: string[] = []
      if (xskill.prior_knowledge_used) parts.push('prior knowledge')
      if (xskill.skill_built) parts.push('skill built')
      if (typeof xskill.experiences_extracted === 'number') parts.push(`${xskill.experiences_extracted} exp`)
      xskillValue = parts.length > 0 ? parts.join(', ') : 'active'
    } else {
      xskillValue = String(xskill)
    }
    badges.push({ label: 'xSkill', value: xskillValue, color: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800', icon: '📚' })
  }
  if (meta.rounds && meta.rounds > 1) {
    badges.push({ label: 'Rounds', value: String(meta.rounds), color: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800', icon: '🗣️' })
  }
  if (meta.usage) {
    const u = meta.usage as Record<string, number>
    if (u.total_tokens) {
      badges.push({ label: 'Tokens', value: String(u.total_tokens), color: 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700', icon: '📊' })
    }
  }

  const elapsedMs = meta.elapsed_ms as number | undefined
  const elapsedLabel = elapsedMs
    ? elapsedMs >= 60000
      ? `${(elapsedMs / 60000).toFixed(1)}m`
      : elapsedMs >= 1000
        ? `${(elapsedMs / 1000).toFixed(1)}s`
        : `${elapsedMs}ms`
    : null
  if (elapsedLabel) {
    badges.push({ label: 'Time', value: elapsedLabel, color: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-800', icon: '⏱️' })
  }

  const taskId = meta.task_id as string | undefined
  const eventsTruncated = meta.events_truncated === true // UI-02

  if (badges.length === 0 && !taskId && !eventsTruncated) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-2 pt-2 border-t border-[#e5e7eb]/50 dark:border-[#2a2a45]/50">
      {badges.map((b) => (
        <span
          key={b.label}
          className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border', b.color)}
        >
          {b.icon && <span>{b.icon}</span>} {b.label}: {b.value}
        </span>
      ))}
      {taskId && (
        <a
          href={`/tasks/${taskId}`}
          className={cn(
            'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border transition-colors cursor-pointer',
            'bg-violet-50 text-violet-700 border-violet-200 hover:bg-violet-100',
            'dark:bg-violet-900/30 dark:text-violet-300 dark:border-violet-800 dark:hover:bg-violet-900/50',
          )}
          title={`Task ${taskId}`}
        >
          <ExternalLink className="w-2.5 h-2.5" />
          Task: {taskId.slice(0, 8)}…
        </a>
      )}
      {/* UI-02: Truncated events notice with link to full task log */}
      {eventsTruncated && taskId && (
        <a
          href={`/tasks/${taskId}`}
          className={cn(
            'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border transition-colors cursor-pointer',
            'bg-sky-50 text-sky-600 border-sky-200 hover:bg-sky-100',
            'dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-800 dark:hover:bg-sky-900/50',
          )}
          title="Execution log was truncated — click to view full log"
        >
          📋 View full execution log →
        </a>
      )}
    </div>
  )
}

// ── Role label map ────────────────────────────────────────────

const ROLE_LABELS: Record<string, string> = {
  user: 'You',
  assistant: 'MekongAI',
  system: 'System',
}

// ── Agent role colors ─────────────────────────────────────────

const AGENT_ROLE_COLORS: Record<string, string> = {
  researcher: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  analyst: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  writer: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  coder: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  reviewer: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
  planner: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  judge: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
}

function getAgentRoleColor(role: string): string {
  const key = (role || '').toLowerCase()
  return AGENT_ROLE_COLORS[key] ?? 'bg-gray-100 text-gray-600 dark:bg-gray-800/50 dark:text-gray-400'
}

// ── Agent Avatar helper ───────────────────────────────────────

function AgentAvatar({ avatar, name, className }: { avatar?: string; name: string; className?: string }) {
  if (avatar) {
    if (avatar.startsWith('http')) {
      return <img src={avatar} alt={name} className={cn('rounded-full object-cover', className ?? 'w-4 h-4')} />
    }
    // Emoji avatar
    return <span className="text-sm leading-none">{avatar}</span>
  }
  return <Bot className={cn('text-blue-500 dark:text-blue-400 shrink-0', className ?? 'w-3.5 h-3.5')} />
}

// ── Agent Attribution Display ─────────────────────────────────

/** Extended agent info that may come from agents_spawned metadata */
interface SpawnedAgentMeta {
  name: string
  role: string
  id?: string
  status?: string
  avatar?: string
  expertise?: string[]
}

function AgentAttribution({ message }: { message: ChatMessage }) {
  const meta = message.metadata
  if (!meta || message.role !== 'assistant') return null

  const agents = meta.agents_spawned as SpawnedAgentMeta[] | undefined
  if (!agents || agents.length === 0) return null

  const primary = agents[0]
  const others = agents.slice(1)

  return (
    <div className="flex items-center gap-1.5 mb-1">
      <AgentAvatar avatar={primary.avatar} name={primary.name} />
      <span className="text-[11px] font-semibold text-blue-700 dark:text-blue-300">
        {primary.name}
      </span>
      {primary.role && (
        <span className={cn(
          'inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-medium leading-none',
          getAgentRoleColor(primary.role),
        )}>
          {primary.role}
        </span>
      )}
      {primary.expertise && primary.expertise.length > 0 && (
        <span
          className="text-[10px] text-gray-400 dark:text-gray-500 ml-0.5 cursor-help"
          title={primary.expertise.join(', ')}
        >
          [{primary.expertise.length} skills]
        </span>
      )}
      {others.length > 0 && (
        <span
          className="text-[10px] text-gray-500 dark:text-gray-400"
          title={others.map((a) => `${a.name} (${a.role})`).join(', ')}
        >
          +{others.length} agent{others.length > 1 ? 's' : ''}
        </span>
      )}
    </div>
  )
}

// ── Copy Button ───────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback for older browsers
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }, [text])

  return (
    <button
      onClick={handleCopy}
      className={cn(
        'absolute top-2 right-2 p-1.5 rounded-md transition-all duration-200',
        'opacity-0 group-hover:opacity-100',
        copied
          ? 'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-400'
          : 'bg-white/80 text-gray-500 hover:text-gray-700 hover:bg-white dark:bg-[#2a2a45]/80 dark:text-gray-400 dark:hover:text-gray-200 dark:hover:bg-[#2a2a45]',
      )}
      title={copied ? 'Đã copy!' : 'Copy nội dung'}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

// ── Component ─────────────────────────────────────────────────

export const MessageBubble = React.memo(function MessageBubble({ message, onRetry }: MessageBubbleProps) {
  const { role, content, timestamp, metadata } = message
  const isUser = role === 'user'
  const isSystem = role === 'system'
  const isError = isSystem && String(content ?? '').startsWith('Error:')

  return (
    <div>
      {/* Main bubble */}
      <div className={cn('flex animate-slide-up', isUser ? 'justify-end' : 'justify-start')}>
        <div className="max-w-[85%] sm:max-w-[75%]">
          {/* Role label */}
          <div className={cn('flex items-center gap-2 mb-1.5', isUser ? 'justify-end' : '')}>
            {!isUser && (
              <div
                className={cn(
                  'w-6 h-6 rounded-[7px] flex items-center justify-center',
                  isSystem
                    ? 'bg-red-50 dark:bg-red-900/30'
                    : 'bg-gradient-to-br from-[#3b82f6] to-[#818cf8]',
                )}
              >
                {isSystem
                  ? <span className="text-[11px]">⚠</span>
                  : <Sparkles className="w-3 h-3 text-white" />}
              </div>
            )}
            <span className="text-[11px] font-semibold text-text-muted dark:text-gray-500 uppercase tracking-wider">
              {ROLE_LABELS[role] ?? role}
            </span>
          </div>

          {/* Agent attribution — show which agent(s) produced this response */}
          {!isUser && !isSystem && <AgentAttribution message={message} />}

          {/* Bubble content */}
          <div
            className={cn(
              'relative group rounded-[14px] px-4 py-3',
              isUser && 'bg-[#1a1a2e] text-white rounded-br-[4px] dark:bg-[#1a1a2e]',
              isSystem && 'bg-red-50 text-red-800 border border-red-200 rounded-bl-[4px] dark:bg-red-900/20 dark:text-red-300 dark:border-red-800',
              !isUser && !isSystem && 'bg-[#f4f5f7] text-text-primary rounded-bl-[4px] dark:bg-[#1e1e35] dark:text-gray-200',
            )}
          >
            {/* Copy button — visible on hover for user & assistant messages */}
            {!isSystem && <CopyButton text={String(content ?? '')} />}

            {/* Thinking/reasoning display (assistant only) */}
            {!isUser && !isSystem && typeof metadata?.thinking === 'string' && metadata.thinking.length > 0 && (
              <ThinkingDisplay
                thinking={metadata.thinking}
                thinkingTokens={typeof metadata.thinking_tokens === 'number' ? metadata.thinking_tokens : undefined}
              />
            )}

            <div className="text-[14px] leading-[1.65] prose-nexus">
              {isUser
                ? <span className="whitespace-pre-wrap break-words">{String(content ?? '')}</span>
                : <MarkdownRenderer content={String(content ?? '')} />}
            </div>

            {/* Metadata badges (assistant only) */}
            <MessageMetadata message={message} />

            {/* Retry button for error messages */}
            {isError && onRetry && (
              <button
                onClick={() => onRetry(String(content ?? ''))}
                className={cn(
                  'mt-1.5 text-[11px] underline flex items-center gap-1',
                  'text-red-600 hover:text-red-800',
                  'dark:text-red-400 dark:hover:text-red-300',
                )}
              >
                ↩ Retry last message
              </button>
            )}
          </div>

          {/* Timestamp */}
          <div className={cn('text-[10px] text-text-muted dark:text-gray-500 mt-1.5', isUser ? 'text-right' : '')}>
            {new Date(timestamp).toLocaleTimeString()}
          </div>
        </div>
      </div>

      {/* Persisted debate rounds from message metadata */}
      {role === 'assistant' && metadata?.debate_rounds &&
        (metadata.debate_rounds as unknown as SwarmRoundEvent[])
          .filter((round: SwarmRoundEvent) => round.proposals?.length > 0)
          .map((round: SwarmRoundEvent, idx: number, arr: SwarmRoundEvent[]) => (
          <InlineDebateCard
            key={round.id || `debate-${round.round}`}
            round={round}
            defaultExpanded={idx === arr.length - 1}
          />
        ))}
    </div>
  )
}, (prev, next) => {
  // Custom comparator: skip re-render if message identity + content unchanged
  if (prev.message.id !== next.message.id) return false
  if (prev.message.content !== next.message.content) return false
  if (prev.message.role !== next.message.role) return false
  // onRetry reference stability is caller's responsibility
  return prev.onRetry === next.onRetry
})
