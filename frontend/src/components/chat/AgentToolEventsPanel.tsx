// ============================================================
// Nexus - AgentToolEventsPanel
// Displays tool execution events for a specific agent in debate
// Shown as a collapsible panel alongside each agent's message
// ============================================================

import { useState } from 'react'
import {
  Wrench,
  CheckCircle2,
  XCircle,
  ChevronDown,
  Search,
  Globe,
  FileText,
  Terminal,
  Code,
  Zap,
} from 'lucide-react'
import { cn } from '../../lib/cn'

// ── Types ─────────────────────────────────────────────────────

export interface AgentToolEvent {
  tool: string
  params_preview?: string
  output_preview?: string
  success?: boolean
  turn: number
  phase?: string // 'preparation' | 'research' | 'action'
  timestamp?: number
}

interface AgentToolEventsPanelProps {
  events: AgentToolEvent[]
  agentName: string
  /** Compact mode for inline display */
  compact?: boolean
}

// ── Tool icon mapping ─────────────────────────────────────────

function getToolIcon(toolName: string) {
  const name = toolName.toLowerCase()
  if (name.includes('search') || name.includes('web_search')) {
    return <Search className="w-3 h-3" />
  }
  if (name.includes('http') || name.includes('request') || name.includes('browse')) {
    return <Globe className="w-3 h-3" />
  }
  if (name.includes('write') || name.includes('read') || name.includes('file')) {
    return <FileText className="w-3 h-3" />
  }
  if (name.includes('command') || name.includes('exec') || name.includes('shell')) {
    return <Terminal className="w-3 h-3" />
  }
  if (name.includes('code') || name.includes('python') || name.includes('javascript')) {
    return <Code className="w-3 h-3" />
  }
  return <Wrench className="w-3 h-3" />
}

// ── Phase badge ───────────────────────────────────────────────

function PhaseBadge({ phase }: { phase?: string }) {
  if (!phase) return null

  const config: Record<string, { bg: string; text: string; label: string }> = {
    preparation: {
      bg: 'bg-amber-100 dark:bg-amber-900/30',
      text: 'text-amber-700 dark:text-amber-300',
      label: 'Prep',
    },
    research: {
      bg: 'bg-blue-100 dark:bg-blue-900/30',
      text: 'text-blue-700 dark:text-blue-300',
      label: 'Research',
    },
    action: {
      bg: 'bg-purple-100 dark:bg-purple-900/30',
      text: 'text-purple-700 dark:text-purple-300',
      label: 'Action',
    },
  }

  const c = config[phase] || config.research
  return (
    <span className={cn('text-[9px] font-medium px-1.5 py-0.5 rounded-full', c.bg, c.text)}>
      {c.label}
    </span>
  )
}

// ── Single tool event row ─────────────────────────────────────

function ToolEventRow({ event, defaultOpen }: { event: AgentToolEvent; defaultOpen?: boolean }) {
  const [expanded, setExpanded] = useState(defaultOpen ?? false)
  const hasOutput = !!event.output_preview

  return (
    <div className="border-l-2 border-gray-200 dark:border-gray-700 pl-2 py-1">
      {/* Header row */}
      <button
        type="button"
        onClick={() => setExpanded((p) => !p)}
        className="flex items-center gap-1.5 w-full text-left group"
      >
        <span className="text-gray-400 dark:text-gray-500 shrink-0">
          {getToolIcon(event.tool)}
        </span>
        <span className="text-[11px] font-mono font-medium text-gray-700 dark:text-gray-300 truncate flex-1">
          {event.tool}
        </span>
        {event.success != null && (
          event.success ? (
            <CheckCircle2 className="w-3 h-3 text-green-500 shrink-0" />
          ) : (
            <XCircle className="w-3 h-3 text-red-400 shrink-0" />
          )
        )}
        <PhaseBadge phase={event.phase} />
        {hasOutput && (
          <ChevronDown
            className={cn(
              'w-3 h-3 text-gray-400 transition-transform duration-150 shrink-0',
              expanded && 'rotate-180',
            )}
          />
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-1 ml-5 space-y-1">
          {event.params_preview && (
            <div className="text-[10px] text-gray-500 dark:text-gray-400 font-mono
              bg-gray-50 dark:bg-gray-900/50 rounded px-2 py-1 break-all
              border border-gray-100 dark:border-gray-800">
              <span className="text-gray-400 dark:text-gray-500 font-sans text-[9px] uppercase tracking-wider">
                params:
              </span>
              <br />
              {event.params_preview}
            </div>
          )}
          {event.output_preview && (
            <div className={cn(
              'text-[10px] font-mono rounded px-2 py-1 break-all border',
              event.success
                ? 'bg-green-50 dark:bg-green-900/10 border-green-100 dark:border-green-800/40 text-green-700 dark:text-green-300'
                : 'bg-red-50 dark:bg-red-900/10 border-red-100 dark:border-red-800/40 text-red-700 dark:text-red-300',
            )}>
              <span className="text-[9px] uppercase tracking-wider font-sans opacity-60">
                result:
              </span>
              <br />
              {event.output_preview}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main Panel ────────────────────────────────────────────────

export function AgentToolEventsPanel({ events, agentName, compact }: AgentToolEventsPanelProps) {
  const [collapsed, setCollapsed] = useState(false)

  if (events.length === 0) return null

  const successCount = events.filter((e) => e.success === true).length
  const failCount = events.filter((e) => e.success === false).length

  if (compact) {
    // Compact inline mode: just show tool badges
    return (
      <div className="flex flex-wrap gap-1 mt-1">
        {events.map((evt, i) => (
          <span
            key={`${evt.tool}-${i}`}
            className={cn(
              'inline-flex items-center gap-0.5 text-[9px] font-medium px-1.5 py-0.5 rounded-full border',
              evt.success
                ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-300'
                : evt.success === false
                  ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-600 dark:text-red-400'
                  : 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400',
            )}
          >
            {getToolIcon(evt.tool)}
            {evt.tool}
          </span>
        ))}
      </div>
    )
  }

  return (
    <div className="w-64 shrink-0 border border-gray-200 dark:border-gray-700/60
      rounded-xl bg-gray-50/80 dark:bg-gray-900/40 overflow-hidden shadow-sm">
      {/* Panel header */}
      <button
        type="button"
        onClick={() => setCollapsed((p) => !p)}
        className={cn(
          'flex items-center gap-2 w-full px-3 py-2 text-left',
          'bg-gray-100/80 dark:bg-gray-800/60 border-b border-gray-200 dark:border-gray-700/50',
          'hover:bg-gray-200/50 dark:hover:bg-gray-800 transition-colors',
        )}
      >
        <Zap className="w-3.5 h-3.5 text-violet-500 dark:text-violet-400 shrink-0" />
        <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-300 flex-1 truncate">
          {agentName} Tools ({events.length})
        </span>
        <span className="flex items-center gap-1">
          {successCount > 0 && (
            <span className="text-[9px] text-green-600 dark:text-green-400 font-medium">
              {successCount}✓
            </span>
          )}
          {failCount > 0 && (
            <span className="text-[9px] text-red-500 dark:text-red-400 font-medium">
              {failCount}✗
            </span>
          )}
        </span>
        <ChevronDown
          className={cn(
            'w-3 h-3 text-gray-400 transition-transform duration-200',
            collapsed && '-rotate-90',
          )}
        />
      </button>

      {/* Tool events list */}
      {!collapsed && (
        <div className="px-2 py-2 space-y-1.5 max-h-auto overflow-y-auto">
          {events.map((evt, i) => (
            <ToolEventRow
              key={`${evt.tool}-${evt.turn}-${i}`}
              event={evt}
              defaultOpen={events.length <= 3}
            />
          ))}
        </div>
      )}
    </div>
  )
}
