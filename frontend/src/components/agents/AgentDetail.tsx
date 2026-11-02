// ============================================================
// Nexus - AgentDetail Component
// Detailed agent information panel with description, personality,
// system_prompt preview, model info, skills, tools, metadata,
// and real-time status from realtimeStore
// ============================================================

import { useState } from 'react'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { useRealtimeStore } from '../../stores/realtimeStore'
import type { Agent } from '../../types/api'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface AgentDetailProps {
  /** Agent data to display */
  agent: Agent
  /** Callback when edit is requested */
  onEdit?: (agent: Agent) => void
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

const ROLE_BG: Record<string, string> = {
  researcher: 'bg-blue-100 dark:bg-blue-900/30',
  developer: 'bg-emerald-100 dark:bg-emerald-900/30',
  analyst: 'bg-amber-100 dark:bg-amber-900/30',
  writer: 'bg-purple-100 dark:bg-purple-900/30',
  coordinator: 'bg-cyan-100 dark:bg-cyan-900/30',
  judge: 'bg-red-100 dark:bg-red-900/30',
  orchestrator: 'bg-indigo-100 dark:bg-indigo-900/30',
  advocate: 'bg-blue-100 dark:bg-blue-900/30',
  critic: 'bg-amber-100 dark:bg-amber-900/30',
  architect: 'bg-indigo-100 dark:bg-indigo-900/30',
  debugger: 'bg-amber-100 dark:bg-amber-900/30',
  assistant: 'bg-blue-100 dark:bg-blue-900/30',
  security_reviewer: 'bg-red-100 dark:bg-red-900/30',
  devops: 'bg-emerald-100 dark:bg-emerald-900/30',
  data_scientist: 'bg-purple-100 dark:bg-purple-900/30',
  ux_designer: 'bg-pink-100 dark:bg-pink-900/30',
}

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
// Helpers
// ---------------------------------------------------------------------------

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

function getRoleBg(role: string) {
  return ROLE_BG[role.toLowerCase()] ?? 'bg-gray-100 dark:bg-gray-800'
}

function getRoleEmoji(role: string, avatar?: string) {
  if (avatar) return avatar
  return ROLE_EMOJI[role.toLowerCase()] ?? '🤖'
}

const THINKING_STYLE_LABELS: Record<string, { label: string; icon: string }> = {
  systematic: { label: 'Systematic', icon: '🔄' },
  creative: { label: 'Creative', icon: '💡' },
  analytical: { label: 'Analytical', icon: '📐' },
  critical: { label: 'Critical', icon: '🎯' },
  pragmatic: { label: 'Pragmatic', icon: '🔨' },
  empirical: { label: 'Empirical', icon: '🔬' },
  empathetic: { label: 'Empathetic', icon: '💝' },
  strategic: { label: 'Strategic', icon: '♟️' },
  pedagogical: { label: 'Pedagogical', icon: '📚' },
}

function getStatus(status: string) {
  return STATUS_CONFIG[status.toLowerCase()] ?? {
    label: status,
    dotClass: 'bg-gray-400 dark:bg-gray-500',
    textClass: 'text-gray-500 dark:text-gray-400',
  }
}

// ---------------------------------------------------------------------------
// Section Header helper
// ---------------------------------------------------------------------------

function SectionHeader({ icon, title }: { icon: string; title: string }) {
  return (
    <h4 className="flex items-center gap-1.5 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">
      <span>{icon}</span>
      {title}
    </h4>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AgentDetail({ agent, onEdit }: AgentDetailProps) {
  const [systemPromptExpanded, setSystemPromptExpanded] = useState(false)

  // Get real-time status from store if available
  const liveStatus = useRealtimeStore((s) => s.agentStatuses[agent.id])
  const effectiveStatus = liveStatus?.status ?? agent.status
  const status = getStatus(effectiveStatus)

  const skillCount = agent.skills ? Object.keys(agent.skills).length : 0
  const toolCount = agent.tools?.length ?? 0
  const hasSystemPrompt = !!agent.system_prompt && agent.system_prompt.length > 0
  const systemPromptPreview = agent.system_prompt?.slice(0, 200) ?? ''

  return (
    <div className="flex-1 overflow-y-auto p-6 lg:p-8 animate-fade-in">
      <div className="max-w-2xl">
        {/* ── Agent Header ────────────────────────────────── */}
        <div className="mb-6">
          <div className="flex items-start gap-3 mb-3">
            <div
              className={cn(
                'w-12 h-12 rounded-xl flex items-center justify-center text-xl shrink-0',
                getRoleBg(agent.role),
              )}
            >
              {getRoleEmoji(agent.role, agent.avatar)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 truncate">
                  {agent.name}
                </h3>
                {onEdit && (
                  <button
                    type="button"
                    onClick={() => onEdit(agent)}
                    className="text-xs text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300 shrink-0"
                    title="Edit agent"
                  >
                    ✏️ Edit
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <Badge variant={getRoleVariant(agent.role)} size="md">
                  {agent.role}
                </Badge>
                <span className="flex items-center gap-1.5 text-sm">
                  <span className={cn('h-2 w-2 rounded-full shrink-0', status.dotClass)} />
                  <span className={status.textClass}>{status.label}</span>
                </span>
                {skillCount > 0 && (
                  <span className="text-[10px] text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 px-1.5 py-0.5 rounded-full">
                    {skillCount} skills
                  </span>
                )}
                {toolCount > 0 && (
                  <span className="text-[10px] text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20 px-1.5 py-0.5 rounded-full">
                    {toolCount} tools
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Live activity indicator */}
          {liveStatus?.last_activity && (
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
              Last activity: {liveStatus.last_activity}
            </p>
          )}
        </div>

        {/* ── Description ──────────────────────────────────── */}
        {agent.description && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="📝" title="Description" />
            <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">
              {agent.description}
            </p>
          </Card>
        )}

        {/* ── Personality ──────────────────────────────────── */}
        {agent.personality && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="🎭" title="Personality" />
            <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed italic">
              "{agent.personality}"
            </p>
          </Card>
        )}

        {/* ── Model Info ──────────────────────────────────── */}
        <Card variant="default" padding="md" className="mb-4">
          <SectionHeader icon="🤖" title="Model" />
          <p
            className="text-sm text-gray-900 dark:text-gray-100 font-mono"
            title={agent.llm_model}
          >
            {formatModelName(agent.llm_model)}
          </p>
          {agent.llm_model !== formatModelName(agent.llm_model) && (
            <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-1 font-mono break-all">
              {agent.llm_model}
            </p>
          )}
        </Card>

        {/* ── Skills ──────────────────────────────────────── */}
        {agent.skills && Object.keys(agent.skills).length > 0 && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="⚡" title="Skills" />
            <div className="flex flex-wrap gap-2">
              {Object.entries(agent.skills).map(([skill, active]) => (
                <Badge
                  key={skill}
                  variant={active ? 'success' : 'default'}
                  size="sm"
                >
                  {active ? '✓' : '✗'} {skill.replace(/_/g, ' ')}
                </Badge>
              ))}
            </div>
          </Card>
        )}

        {/* ── Tools ───────────────────────────────────────── */}
        {agent.tools && agent.tools.length > 0 && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="🔧" title="Tools" />
            <div className="flex flex-wrap gap-2">
              {agent.tools.map((tool) => (
                <Badge key={tool} variant="purple" size="sm">
                  🔧 {tool}
                </Badge>
              ))}
            </div>
          </Card>
        )}

        {/* ── Expertise ────────────────────────────────────── */}
        {agent.expertise && agent.expertise.length > 0 && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="🎯" title="Expertise" />
            <div className="flex flex-wrap gap-2">
              {agent.expertise.map((exp) => (
                <Badge key={exp} variant="info" size="sm">
                  {exp.replace(/_/g, ' ')}
                </Badge>
              ))}
            </div>
          </Card>
        )}

        {/* ── Thinking Style ───────────────────────────────── */}
        {agent.thinking_style && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="🧠" title="Thinking Style" />
            <div className="flex items-center gap-2">
              <span className="text-lg">
                {THINKING_STYLE_LABELS[agent.thinking_style]?.icon ?? '🧠'}
              </span>
              <span className="text-sm text-gray-700 dark:text-gray-300 font-medium">
                {THINKING_STYLE_LABELS[agent.thinking_style]?.label ?? agent.thinking_style}
              </span>
            </div>
          </Card>
        )}

        {/* ── Background ───────────────────────────────────── */}
        {agent.background && (
          <Card variant="default" padding="md" className="mb-4">
            <SectionHeader icon="📖" title="Background" />
            <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">
              {agent.background}
            </p>
          </Card>
        )}

        {/* ── System Prompt (collapsible) ──────────────────── */}
        {hasSystemPrompt && (
          <Card variant="default" padding="md" className="mb-4">
            <div className="flex items-center justify-between mb-2">
              <SectionHeader icon="📋" title="System Prompt" />
              <button
                type="button"
                onClick={() => setSystemPromptExpanded(!systemPromptExpanded)}
                className="text-xs text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300"
              >
                {systemPromptExpanded ? 'Collapse ▲' : 'Expand ▼'}
              </button>
            </div>
            <div
              className={cn(
                'text-xs text-gray-600 dark:text-gray-400 font-mono leading-relaxed whitespace-pre-wrap',
                !systemPromptExpanded && 'max-h-[80px] overflow-hidden relative',
              )}
            >
              {systemPromptExpanded ? agent.system_prompt : systemPromptPreview}
              {!systemPromptExpanded && agent.system_prompt && agent.system_prompt.length > 200 && (
                <span className="text-gray-400 dark:text-gray-500">…</span>
              )}
              {!systemPromptExpanded && (
                <div className="absolute bottom-0 left-0 right-0 h-6 bg-gradient-to-t from-white dark:from-[#1a1a2e] to-transparent" />
              )}
            </div>
          </Card>
        )}

        {/* ── Metadata ────────────────────────────────────── */}
        <Card variant="default" padding="md" className="mb-4">
          <SectionHeader icon="ℹ️" title="Details" />
          <div className="text-xs text-gray-500 dark:text-gray-400 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-medium">ID</span>
              <span className="font-mono text-gray-600 dark:text-gray-300 select-all">{agent.id}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="font-medium">Created</span>
              <span className="text-gray-600 dark:text-gray-300">
                {agent.created_at
                  ? new Date(agent.created_at).toLocaleString()
                  : 'N/A'}
              </span>
            </div>
            {agent.swarm_id && (
              <div className="flex items-center justify-between">
                <span className="font-medium">Swarm</span>
                <span className="font-mono text-gray-600 dark:text-gray-300">{agent.swarm_id}</span>
              </div>
            )}
            {agent.parent_agent_id && (
              <div className="flex items-center justify-between">
                <span className="font-medium">Parent Agent</span>
                <span className="font-mono text-gray-600 dark:text-gray-300">{agent.parent_agent_id}</span>
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
