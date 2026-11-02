// ============================================================
// Nexus - AgentMonitor (Enhanced)
// Split-view agent monitoring with search, filters, grid/list
// toggle, AgentCard/AgentDetail sub-components, SpawnAgent,
// real-time updates from realtimeStore, usePolling, dark mode
// ============================================================

import { useState, useMemo, useCallback } from 'react'
import { cn } from '../lib/cn'
import { apiGet } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { useRealtimeStore } from '../stores/realtimeStore'
import { useTranslation } from '../i18n'
import { AgentCard } from './agents/AgentCard'
import { AgentDetail } from './agents/AgentDetail'
import { SpawnAgentDialog } from './agents/SpawnAgentDialog'
import { Badge } from './ui/Badge'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import type { Agent } from '../types/api'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL = 5_000

const STATUS_OPTIONS = ['all', 'active', 'busy', 'working', 'idle', 'paused', 'terminated'] as const
const ROLE_OPTIONS = ['all', 'orchestrator', 'researcher', 'developer', 'analyst', 'writer', 'coordinator', 'judge', 'critic', 'architect', 'debugger', 'assistant', 'security_reviewer', 'devops', 'data_scientist', 'ux_designer'] as const

type ViewMode = 'list' | 'grid'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AgentMonitor() {
  // ── i18n ───────────────────────────────────────────────
  const { t } = useTranslation()

  // ── State ──────────────────────────────────────────────
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [roleFilter, setRoleFilter] = useState<string>('all')
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)
  const [spawnDialogOpen, setSpawnDialogOpen] = useState(false)

  // ── Responsive ─────────────────────────────────────────
  const isDesktop = useMediaQuery('(min-width: 640px)')

  // ── Realtime agent statuses ────────────────────────────
  const liveAgentStatuses = useRealtimeStore((s) => s.agentStatuses)

  // ── Data fetching ──────────────────────────────────────
  const { data: agents, loading, error, refresh } = usePolling<Agent[]>({
    fetcher: () => apiGet<Agent[]>('/agents/'),
    interval: POLL_INTERVAL,
  })

  const agentList = agents ?? []

  // ── Merge live statuses into agent data ────────────────
  const mergedAgents = useMemo(() => {
    return agentList.map((agent) => {
      const live = liveAgentStatuses[agent.id]
      if (live) {
        return { ...agent, status: live.status, name: live.name || agent.name }
      }
      return agent
    })
  }, [agentList, liveAgentStatuses])

  // ── Filtered agents ────────────────────────────────────
  const filteredAgents = useMemo(() => {
    return mergedAgents.filter((a) => {
      // Search filter (now includes description)
      const q = searchQuery.toLowerCase()
      if (q && !a.name.toLowerCase().includes(q) && !a.role.toLowerCase().includes(q) && !(a.description ?? '').toLowerCase().includes(q)) {
        return false
      }
      // Status filter
      if (statusFilter !== 'all' && a.status.toLowerCase() !== statusFilter) {
        return false
      }
      // Role filter
      if (roleFilter !== 'all' && a.role.toLowerCase() !== roleFilter) {
        return false
      }
      return true
    })
  }, [mergedAgents, searchQuery, statusFilter, roleFilter])

  // ── Handlers ───────────────────────────────────────────
  const handleSelectAgent = useCallback(
    async (agent: Agent) => {
      // Fetch full agent details (includes system_prompt)
      try {
        const fullAgent = await apiGet<Agent>(`/agents/${agent.id}`)
        setSelectedAgent(fullAgent)
      } catch {
        // Fallback to list data if detail fetch fails
        setSelectedAgent(agent)
      }
      if (!isDesktop) {
        setMobileDetailOpen(true)
      }
    },
    [isDesktop],
  )

  const handleCloseMobileDetail = useCallback(() => {
    setMobileDetailOpen(false)
  }, [])

  const handleAgentSpawned = useCallback(() => {
    refresh()
  }, [refresh])

  // ── Status counts for badges ───────────────────────────
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    mergedAgents.forEach((a) => {
      const key = a.status.toLowerCase()
      counts[key] = (counts[key] ?? 0) + 1
    })
    return counts
  }, [mergedAgents])

  // ── Render ─────────────────────────────────────────────
  return (
    <div className="flex h-full">
      {/* ════════════════════════════════════════════════════
          LEFT PANEL: Agent List
          ════════════════════════════════════════════════════ */}
      <div
        className={cn(
          'w-full sm:w-96 border-r border-gray-200 dark:border-[#2a2a45] flex flex-col shrink-0',
          'bg-white dark:bg-[#0f0f23]',
        )}
      >
        {/* ── Header ──────────────────────────────────────── */}
        <div className="px-4 py-4 border-b border-gray-200 dark:border-[#2a2a45] space-y-3">
          {/* Title + count + Spawn button */}
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Agents
            </h2>
            <div className="flex items-center gap-2">
              <Badge variant="brand" size="sm">
                {mergedAgents.length}
              </Badge>
              {statusCounts['active'] && (
                <Badge variant="success" size="sm" dot>
                  {statusCounts['active']} active
                </Badge>
              )}
              <button
                type="button"
                onClick={() => setSpawnDialogOpen(true)}
                className={cn(
                  'px-2.5 py-1 text-xs font-medium rounded-lg transition-colors',
                  'bg-brand-600 text-white hover:bg-brand-700',
                )}
                title="Spawn new agent"
              >
                + Spawn
              </button>
            </div>
          </div>

          {/* Search */}
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search agents by name, role, or description..."
            className={cn(
              'w-full text-sm py-2 px-3 rounded-lg border transition-colors',
              'border-gray-200 bg-gray-50 text-gray-900 placeholder-gray-400',
              'focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500',
              'dark:border-[#2a2a45] dark:bg-[#1a1a2e] dark:text-gray-100 dark:placeholder-gray-500',
            )}
          />

          {/* Filters row */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Status filter */}
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className={cn(
                'text-xs py-1.5 px-2 rounded-md border',
                'border-gray-200 bg-white text-gray-700',
                'dark:border-[#2a2a45] dark:bg-[#1a1a2e] dark:text-gray-300',
                'focus:outline-none focus:ring-2 focus:ring-brand-500',
              )}
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s === 'all' ? 'All Status' : s.charAt(0).toUpperCase() + s.slice(1)}
                </option>
              ))}
            </select>

            {/* Role filter */}
            <select
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
              className={cn(
                'text-xs py-1.5 px-2 rounded-md border',
                'border-gray-200 bg-white text-gray-700',
                'dark:border-[#2a2a45] dark:bg-[#1a1a2e] dark:text-gray-300',
                'focus:outline-none focus:ring-2 focus:ring-brand-500',
              )}
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r === 'all' ? 'All Roles' : r.charAt(0).toUpperCase() + r.slice(1)}
                </option>
              ))}
            </select>

            {/* Spacer */}
            <div className="flex-1" />

            {/* View mode toggle */}
            <div className="flex items-center border border-gray-200 dark:border-[#2a2a45] rounded-md overflow-hidden">
              <button
                type="button"
                onClick={() => setViewMode('list')}
                className={cn(
                  'px-2 py-1 text-xs transition-colors',
                  viewMode === 'list'
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-400'
                    : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] dark:text-gray-400',
                )}
                title="List view"
              >
                ☰
              </button>
              <button
                type="button"
                onClick={() => setViewMode('grid')}
                className={cn(
                  'px-2 py-1 text-xs transition-colors',
                  viewMode === 'grid'
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-400'
                    : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-[#1a1a2e] dark:text-gray-400',
                )}
                title="Grid view"
              >
                ⊞
              </button>
            </div>
          </div>
        </div>

        {/* ── Agent List / Grid ───────────────────────────── */}
        <div className="flex-1 overflow-y-auto p-3">
          {/* Loading state */}
          {loading && agentList.length === 0 && (
            <div className="flex items-center justify-center py-16">
              <Spinner size="md" className="text-brand-500" />
            </div>
          )}

          {/* Error state */}
          {error && (
            <EmptyState
              icon="⚠️"
              title="Failed to load agents"
              description={error}
              action={{ label: 'Retry', onClick: refresh }}
            />
          )}

          {/* Empty state */}
          {!loading && !error && filteredAgents.length === 0 && (
            <EmptyState
              icon="🤖"
              title={searchQuery || statusFilter !== 'all' || roleFilter !== 'all' ? t('agents.noMatchingAgents') : t('agents.noAgents')}
              description={
                searchQuery || statusFilter !== 'all' || roleFilter !== 'all'
                  ? t('agents.noMatchingHint')
                  : t('agents.noAgentsHint')
              }
            />
          )}

          {/* Agent cards */}
          <div
            className={cn(
              viewMode === 'grid'
                ? 'grid grid-cols-2 gap-2'
                : 'space-y-2',
            )}
          >
            {filteredAgents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                isSelected={selectedAgent?.id === agent.id}
                onClick={() => handleSelectAgent(agent)}
              />
            ))}
          </div>
        </div>
      </div>

      {/* ════════════════════════════════════════════════════
          RIGHT PANEL: Agent Detail (desktop)
          ════════════════════════════════════════════════════ */}
      <div
        className={cn(
          'hidden sm:flex flex-1 flex-col',
          'bg-gray-50 dark:bg-[#0f0f23]',
        )}
      >
        {selectedAgent ? (
          <AgentDetail agent={selectedAgent} />
        ) : (
          <EmptyState
            icon="🤖"
            title={t('agents.selectAgent')}
            description={t('agents.selectAgentHint')}
            className="h-full"
          />
        )}
      </div>

      {/* ════════════════════════════════════════════════════
          MOBILE DETAIL OVERLAY
          ════════════════════════════════════════════════════ */}
      {!isDesktop && mobileDetailOpen && selectedAgent && (
        <div className="fixed inset-0 z-50 flex flex-col bg-white dark:bg-[#0f0f23]">
          {/* Mobile header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-[#2a2a45]">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
              {selectedAgent.name}
            </h3>
            <button
              type="button"
              onClick={handleCloseMobileDetail}
              className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 text-lg"
            >
              ✕
            </button>
          </div>
          <AgentDetail agent={selectedAgent} />
        </div>
      )}

      {/* ════════════════════════════════════════════════════
          SPAWN DIALOG
          ════════════════════════════════════════════════════ */}
      <SpawnAgentDialog
        isOpen={spawnDialogOpen}
        onClose={() => setSpawnDialogOpen(false)}
        onSpawned={handleAgentSpawned}
      />
    </div>
  )
}
