// ============================================================
// MekongAI Nexus - SystemConfig Dashboard Component
// Displays system configuration and architecture components
// with color-coded values and status indicators
// ============================================================

import { cn } from '../../lib/cn'
import { Card } from '../ui/Card'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SystemInfo {
  version?: string
  core_llm?: string
  agent_llm?: string
  local_llm_enabled?: boolean
  ironclaw_enabled?: boolean
  docker_sandbox?: boolean
  env?: string
  [key: string]: unknown
}

export interface SystemConfigProps {
  /** System info data from API */
  info: SystemInfo | null
  /** Additional CSS classes */
  className?: string
}

// ---------------------------------------------------------------------------
// Architecture components (hardcoded)
// ---------------------------------------------------------------------------

const architectureComponents = [
  { name: 'Core Orchestrator', icon: '🧠', color: 'text-blue-600 dark:text-blue-400' },
  { name: 'Agent Spawner', icon: '🤖', color: 'text-emerald-600 dark:text-emerald-400' },
  { name: 'Swarm Engine', icon: '🐟', color: 'text-purple-600 dark:text-purple-400' },
  { name: 'Message Bus', icon: '💬', color: 'text-amber-600 dark:text-amber-400' },
  { name: 'MCP Registry', icon: '🔧', color: 'text-indigo-600 dark:text-indigo-400' },
  { name: 'Memory Manager', icon: '🧬', color: 'text-pink-600 dark:text-pink-400' },
  { name: 'Coding Engine', icon: '💻', color: 'text-teal-600 dark:text-teal-400' },
  { name: 'Sandbox Manager', icon: '🛡️', color: 'text-orange-600 dark:text-orange-400' },
  { name: 'GraphRAG', icon: '📊', color: 'text-cyan-600 dark:text-cyan-400' },
] as const

// ---------------------------------------------------------------------------
// ConfigItem sub-component
// ---------------------------------------------------------------------------

interface ConfigItemProps {
  label: string
  value: string
  valueClassName?: string
}

function ConfigItem({ label, value, valueClassName }: ConfigItemProps) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3 bg-gray-50 dark:bg-[#1f1f38] rounded-lg">
      <span className="text-xs font-medium text-text-muted dark:text-gray-500 uppercase tracking-wider">
        {label}
      </span>
      <span className={cn('text-sm', valueClassName || 'text-text-primary dark:text-gray-200')}>
        {value}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SystemConfig({ info, className }: SystemConfigProps) {
  if (!info) {
    return null
  }

  const sandboxLabel = info.ironclaw_enabled
    ? 'IronClaw WASM'
    : info.docker_sandbox
      ? 'Docker'
      : 'Process'

  return (
    <Card variant="default" padding="md" className={className}>
      <h3 className="font-display text-base font-semibold text-text-dark dark:text-gray-100 mb-4">
        ⚙️ System Configuration
      </h3>

      {/* Config grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
        <ConfigItem
          label="Version"
          value={info.version || 'N/A'}
        />
        <ConfigItem
          label="Core LLM"
          value={info.core_llm || 'N/A'}
          valueClassName="text-brand-600 dark:text-brand-400 font-medium"
        />
        <ConfigItem
          label="Agent LLM"
          value={info.agent_llm || 'N/A'}
          valueClassName="text-purple-600 dark:text-purple-400 font-medium"
        />
        <ConfigItem
          label="Local LLM"
          value={info.local_llm_enabled ? 'Enabled' : 'Disabled'}
          valueClassName={
            info.local_llm_enabled
              ? 'text-emerald-600 dark:text-emerald-400 font-medium'
              : 'text-text-muted dark:text-gray-500'
          }
        />
        <ConfigItem
          label="Sandbox"
          value={sandboxLabel}
          valueClassName="text-amber-600 dark:text-amber-400 font-medium"
        />
        <ConfigItem
          label="Environment"
          value={info.env || 'production'}
        />
      </div>

      {/* Architecture components */}
      <h4 className="font-display text-sm font-semibold text-text-dark dark:text-gray-200 mb-3">
        🏗️ Architecture Components
      </h4>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {architectureComponents.map((comp) => (
          <div
            key={comp.name}
            className={cn(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg',
              'bg-gray-50 dark:bg-[#1f1f38]',
              'hover:bg-gray-100 dark:hover:bg-[#252540]',
              'transition-colors duration-150',
            )}
          >
            <span className="text-lg shrink-0" aria-hidden="true">{comp.icon}</span>
            <span className="text-sm font-medium text-text-primary dark:text-gray-200 flex-1">
              {comp.name}
            </span>
            {/* Active status dot */}
            <span className="relative flex h-2 w-2 shrink-0">
              <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
          </div>
        ))}
      </div>
    </Card>
  )
}
