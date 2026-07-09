// ============================================================
// MekongAI Nexus - AgentCard Component
// Compact card for agent grid/list view with role badge,
// status dot, description snippet, skill/tool counts,
// model name, hover effect, and dark mode support
// ============================================================

import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import type { Agent } from '../../types/api'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface AgentCardProps {
  /** Agent data to display */
  agent: Agent
  /** Whether this card is currently selected */
  isSelected?: boolean
  /** Click handler for selecting agent */
  onClick?: () => void
}

// ---------------------------------------------------------------------------
// Role color mapping
// ---------------------------------------------------------------------------

const ROLE_VARIANT: Record<string, 'info' | 'success' | 'warning' | 'purple' | 'error' | 'brand' | 'default'> = {
  researcher: 'info',
  developer: 'success',
  analyst: 'warning',
  writer: 'purple',
  coordinator: 'brand',
  judge: 'error',
  orchestrator: 'brand',
  advocate: 'info',
  critic: 'warning',
  architect: 'brand',
  debugger: 'warning',
  assistant: 'info',
  security_reviewer: 'error',
  devops: 'success',
  data_scientist: 'purple',
  ux_designer: 'purple',
}

// ---------------------------------------------------------------------------
// Status mapping
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<string, { label: string; dotClass: string; textClass: string }> = {
  idle: {
    label: 'Idle',
    dotClass: 'bg-gray-400 dark:bg-gray-500',
    textClass: 'text-gray-500 dark:text-gray-400',
  },
  active: {
    label: 'Active',
    dotClass: 'bg-emerald-500 animate-pulse',
    textClass: 'text-emerald-600 dark:text-emerald-400',
  },
  busy: {
    label: 'Busy',
    dotClass: 'bg-blue-500 animate-pulse',
    textClass: 'text-blue-600 dark:text-blue-400',
  },
  working: {
    label: 'Working',
    dotClass: 'bg-blue-500 animate-pulse',
    textClass: 'text-blue-600 dark:text-blue-400',
  },
  paused: {
    label: 'Paused',
    dotClass: 'bg-amber-500',
    textClass: 'text-amber-600 dark:text-amber-400',
  },
  terminated: {
    label: 'Terminated',
    dotClass: 'bg-red-400',
    textClass: 'text-red-600 dark:text-red-400',
  },
}

// ---------------------------------------------------------------------------
// Role emoji mapping
// ---------------------------------------------------------------------------

const ROLE_EMOJI: Record<string, string> = {
  researcher: '🔍',
  developer: '💻',
  analyst: '📊',
  writer: '✍️',
  coordinator: '📋',
  judge: '⚖️',
  orchestrator: '🧠',
  advocate: '📣',
  critic: '🔎',
  architect: '🏗️',
  debugger: '🪲',
  assistant: '❓',
  security_reviewer: '🔒',
  devops: '🐳',
  data_scientist: '🔬',
  ux_designer: '🎨',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Shorten long provider/model paths for display */
function formatModelName(model: string): string {
  if (!model || model === 'auto' || model === 'gpt-4o-mini') return model
  const dotParts = model.split('.')
  if (dotParts.length >= 3) return dotParts.slice(1).join('.')
  if (model.includes('/')) {
    const slashParts = model.split('/')
    return slashParts[slashParts.length - 1]
  }
  return model
}

function getRoleVariant(role: string) {
  return ROLE_VARIANT[role.toLowerCase()] ?? 'default'
}

function getStatus(status: string) {
  return STATUS_CONFIG[status.toLowerCase()] ?? {
    label: status,
    dotClass: 'bg-gray-400 dark:bg-gray-500',
    textClass: 'text-gray-500 dark:text-gray-400',
  }
}

function getRoleEmoji(role: string, avatar?: string): string {
  if (avatar) return avatar
  return ROLE_EMOJI[role.toLowerCase()] ?? '🤖'
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AgentCard({ agent, isSelected = false, onClick }: AgentCardProps) {
  const status = getStatus(agent.status)
  const skillCount = agent.skills ? Object.keys(agent.skills).length : 0
  const toolCount = agent.tools?.length ?? 0
  const expertisePreview = (agent.expertise ?? []).slice(0, 2)

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full text-left p-3.5 rounded-lg transition-all duration-200',
        'border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
        isSelected
          ? 'bg-brand-50 border-brand-200 shadow-sm dark:bg-brand-900/20 dark:border-brand-700'
          : [
              'bg-white border-transparent',
              'hover:bg-gray-50 hover:border-gray-200 hover:shadow-sm',
              'dark:bg-[#1a1a2e] dark:hover:bg-[#22223a] dark:hover:border-[#3a3a55]',
            ],
      )}
    >
      {/* Row 1: Emoji + Name + Status */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-base shrink-0" title={agent.role}>
          {getRoleEmoji(agent.role, agent.avatar)}
        </span>
        <span className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate flex-1">
          {agent.name}
        </span>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className={cn('h-2 w-2 rounded-full', status.dotClass)} />
          <span className={cn('text-xs', status.textClass)}>{status.label}</span>
        </div>
      </div>

      {/* Row 2: Description snippet */}
      {(agent.description || agent.personality) && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-1 mb-1.5 leading-relaxed">
          {agent.description || agent.personality}
        </p>
      )}

      {/* Row 3: Role badge + Expertise tags + Skill/Tool counts + Model */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <Badge variant={getRoleVariant(agent.role)} size="sm">
          {agent.role}
        </Badge>
        {expertisePreview.map((exp) => (
          <span
            key={exp}
            className="text-[10px] text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 px-1.5 py-0.5 rounded-full"
          >
            {exp.replace(/_/g, ' ')}
          </span>
        ))}
        {(agent.expertise?.length ?? 0) > 2 && (
          <span className="text-[10px] text-gray-400 dark:text-gray-500">
            +{(agent.expertise?.length ?? 0) - 2}
          </span>
        )}
        {skillCount > 0 && !expertisePreview.length && (
          <span className="text-[10px] text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 px-1.5 py-0.5 rounded-full">
            {skillCount} skills
          </span>
        )}
        {toolCount > 0 && !expertisePreview.length && (
          <span className="text-[10px] text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20 px-1.5 py-0.5 rounded-full">
            {toolCount} tools
          </span>
        )}
        <span className="flex-1" />
        <span
          className="text-[10px] text-gray-400 dark:text-gray-500 truncate max-w-[120px]"
          title={agent.llm_model}
        >
          {formatModelName(agent.llm_model)}
        </span>
      </div>
    </button>
  )
}
